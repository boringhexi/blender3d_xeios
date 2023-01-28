import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Image, ShaderNodeCustomGroup


class MakeImageChannelPackedOperator(bpy.types.Operator):
    bl_idname = "xeiosshader.make_image_channel_packed"
    bl_label = "Make Image channel packed"
    bl_description = (
        "Make the Image use Channel Packed alpha mode (for accurate rendering)"
    )

    image_name: StringProperty()

    def execute(self, context):
        print("Make channel packed (button pressed)")
        print(self.image_name)
        return {"FINISHED"}


class FixMaterialAlphaModeOperator(bpy.types.Operator):
    bl_idname = "xeiosshader.fix_material_alpha_mode"
    bl_label = "Fix Material alpha mode"
    bl_description = "Change the Material to right alpha mode (for accurate rendering)"

    needs_opaque: BoolProperty()

    def execute(self, context):
        print("Needs Opaque?")
        print(self.needs_opaque)
        return {"FINISHED"}


class XeiosShaderNode(ShaderNodeCustomGroup):
    bl_name = "XeiosShaderNode"
    bl_label = "Xeios Shader"

    shadingtypes = [
        ("SHADED", "Shaded", "Regular shading"),
        ("UNSHADED", "Unshaded", "Unshaded/full brightness"),
    ]

    blendtypes = [
        ("NORMAL", "Normal", "Regular blend"),
        ("ADD", "Add", "Additive blend"),
        ("MULTIPLY", "Multiply", "Multiply blend"),
        ("SUBTRACT", "Subtract", "Subtractive blend"),
    ]

    colormodes = [
        (
            "DIFFUSESPECULAR",
            "Diffuse + Specular",
            "Use diffuse and specular colors",
        ),
        (
            "VERTEXCOLORS",
            "Vertex Colors",
            "Use mesh's Color Attributes to color the vertices",
        ),
    ]

    def vertexcolor_items(self, context):
        if context.active_object.type == "MESH":
            me = context.active_object.data
            vcol_layer_names = [x.name for x in me.color_attributes]
            return [(x.upper(), x, "") for x in vcol_layer_names]
        else:
            return []

    select_shadingtype: EnumProperty(description="Shading type", items=shadingtypes)
    select_blendtype: EnumProperty(description="Blend type", items=blendtypes)
    select_colormode: EnumProperty(
        description="Color mode",
        items=colormodes,
    )
    select_diffusecolor: FloatVectorProperty(
        name="",
        description="Diffuse color",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
    )
    select_specularcolor: FloatVectorProperty(
        name="",
        description="Specular color",
        subtype="COLOR",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0),
    )
    select_specularexp: FloatProperty(
        name="", description="Specular exponent", min=0.0, max=1024.0, default=1.0
    )
    select_vertexcolors: EnumProperty(
        name="",
        description="Color attribute layer to use for vertex colors",
        items=vertexcolor_items,
    )
    select_teximage: PointerProperty(
        type=Image, name="", description="Image to use for the texture"
    )
    select_texreflect: BoolProperty(
        name="", description="Use reflective texture mapping", default=False
    )
    select_alpha: BoolProperty(
        name="", description="Enable alpha transparency for the material", default=False
    )

    def _create_node_tree(self):
        """create all the nodes we could potentially need"""
        self.node_tree.outputs.new("NodeSocketShader", "Shader")
        pass  # TODO

    def _reconnect_node_tree(self) -> None:
        """reconnect the nodes based on the current blendtype/shadingtype and such"""
        pass  # TODO

    def init(self, context):
        self.node_tree = bpy.data.node_groups.new("." + self.bl_name, "ShaderNodeTree")
        self.node_tree.nodes.new("NodeGroupOutput")
        self._create_node_tree()
        self._reconnect_node_tree()
        self.width = 200

    def draw_buttons(self, context, layout):
        row = layout.row()
        row.alert = self.select_shadingtype == "None"
        row.prop(self, "select_shadingtype", text="")

        row = layout.row()
        row.alert = self.select_blendtype == "None"
        row.prop(self, "select_blendtype", text="")

        box = layout.box()
        row = box.row()
        row.alert = self.select_colormode == "None"
        row.prop(self, "select_colormode", text="")

        if self.select_colormode == "DIFFUSESPECULAR":
            self._draw_diffusespecular(box)
        elif self.select_colormode == "VERTEXCOLORS":
            self._draw_vertexcolors(context, box)

        box = layout.box()
        row = box.row()
        row.prop(self, "select_teximage", text="")
        self._draw_needs_channel_packed(
            box, self.select_teximage, self.select_blendtype, self.select_alpha
        )
        row = box.row()
        row.prop(self, "select_texreflect", text="Reflective")

        box = layout.box()
        row = box.row()
        row.prop(self, "select_alpha", text="Alpha transparency")
        self._draw_needs_mat_alpha_mode(
            context, box, self.select_blendtype, self.select_alpha
        )

    def _draw_diffusespecular(self, box):
        row = box.row()
        row.prop(self, "select_diffusecolor", text="Diffuse")

        row = box.row()
        # Specular color and exponent, side by side
        row.prop(self, "select_specularcolor", text="Specular")
        column = row.column()
        column.prop(self, "select_specularexp", text="")

    def _draw_vertexcolors(self, context, box):
        row = box.row()
        ob = context.active_object
        if ob.type == "MESH" and ob.data.color_attributes:
            row.prop(self, "select_vertexcolors", text="", icon="GROUP_VCOL")
        else:
            row.alert = True
            row.label(text="No vertex colors found")

    def _draw_needs_channel_packed(
        self, box, select_teximage, select_blendtype, select_alpha
    ):
        image_name = select_teximage.name if select_teximage else ""
        needs_channel_packed = select_blendtype in ("MULTIPLY", "SUBTRACT") or (
            not select_alpha and select_blendtype != "ADD"
        )
        is_channel_packed = (
            bpy.data.images[image_name].alpha_mode == "CHANNEL_PACKED"
            if image_name in bpy.data.images.keys()
            else False
        )
        if select_teximage and needs_channel_packed and not is_channel_packed:
            row = box.row()
            row.alert = True
            row.label(text="Image alpha mode should be Channel Packed")
            row = box.row()
            row.alert = True
            op_props = row.operator(
                "xeiosshader.make_image_channel_packed", text="Click here to fix this"
            )
            op_props.image_name = image_name

    def _draw_needs_mat_alpha_mode(self, context, box, select_blendtype, select_alpha):
        needs_alphablend = select_alpha or select_blendtype in (
            "ADD",
            "MULTIPLY",
            "SUBTRACT",
        )
        needs_opaque = not needs_alphablend
        mat_blendmethod = context.active_object.active_material.blend_method
        if (needs_alphablend and mat_blendmethod != "BLEND") or (
            needs_opaque and mat_blendmethod != "OPAQUE"
        ):
            row = box.row()
            row.alert = True
            row.label(
                text="Material alpha mode should be "
                + ("Opaque" if needs_opaque else "Alpha Blend")
            )
            row = box.row()
            row.alert = True
            op_props = row.operator(
                "xeiosshader.fix_material_alpha_mode", text="Click here to fix this"
            )
            op_props.needs_opaque = needs_opaque

    def copy(self, node: "XeiosShaderNode"):
        self.node_tree = node.node_tree.copy()

    def free(self):
        bpy.data.node_groups.remove(self.node_tree, do_unlink=True)
