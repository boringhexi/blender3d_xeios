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
    bl_description = "Make the Image use Channel Packed alpha mode (for accurate display in Eevee/Cycles)"

    image_name: StringProperty()

    def execute(self, context):
        if self.image_name in bpy.data.images.keys():
            image = bpy.data.images[self.image_name]
            image.alpha_mode = "CHANNEL_PACKED"
        return {"FINISHED"}


class FixMaterialAlphaModeOperator(bpy.types.Operator):
    bl_idname = "xeiosshader.fix_material_alpha_mode"
    bl_label = "Fix Material alpha mode"
    bl_description = (
        "Change the Material to correct alpha mode (for accurate display in Eevee)"
    )

    needs_opaque: BoolProperty()

    def execute(self, context):
        material = context.active_object.active_material
        if self.needs_opaque:
            material.blend_method = "OPAQUE"
        else:  # Otherwise, needs Alpha Blend
            material.blend_method = "BLEND"
        return {"FINISHED"}


class EnableShowBackfaceOperator(bpy.types.Operator):
    bl_idname = "xeiosshader.enable_show_backface"
    bl_label = "Enable Show Backface"
    bl_description = (
        "For the Material, enable Show Backface (for accurate display in Eevee)"
    )

    def execute(self, context):
        material = context.active_object.active_material
        material.show_transparent_back = True
        return {"FINISHED"}


class XeiosShaderNode(ShaderNodeCustomGroup):
    bl_name = "XeiosShaderNode"
    bl_label = "Xeios Shader"

    shadingtypes = [
        ("SHADED", "Shaded", "Regular shading"),
        ("UNSHADED", "Unshaded", "Unshaded/full brightness"),
        (
            "VERTEXCOLORS",
            "Vertex colors",
            "Use mesh's Color Attributes to color the vertices",
        ),
    ]

    blendtypes = [
        ("MIX", "Mix", "Regular blend"),
        ("ADD", "Add", "Additive blend"),
        (
            "INVMULTIPLY",
            "InvMultiply",
            "Multiply by inverse blend (displays wrong in Eevee/Cycles)",
        ),
        ("SUBTRACT", "Subtract", "Subtractive blend"),
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
    select_diffusecolor: FloatVectorProperty(
        name="",
        description="Diffuse color (doesn't display in Eevee/Cycles)",
        subtype="COLOR",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0),
    )
    select_specularcolor: FloatVectorProperty(
        name="",
        description="Specular color (doesn't display in Eevee/Cycles)",
        subtype="COLOR",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0),
    )
    select_specularexp: FloatProperty(
        name="",
        description="Specular exponent (doesn't display in Eevee/Cycles)",
        min=0.0,
        max=1024.0,
        default=1.0,
    )
    select_basecolor: FloatVectorProperty(
        name="",
        description="Base material color",
        subtype="COLOR",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0),
    )
    select_basealpha: FloatProperty(
        name="",
        description="Base material alpha",
        min=0.0,
        max=1.0,
        default=1.0,
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
        self.width = 260

    def draw_buttons(self, context, layout):
        row = layout.row()
        box = row.box()
        row = box.row()
        row.alert = self.select_shadingtype == "None"
        row.prop(self, "select_shadingtype", expand=True)
        if self.select_shadingtype == "SHADED":
            self._draw_shadedcolors(box)
        elif self.select_shadingtype == "UNSHADED":
            self._draw_unshadedcolors(box)
        elif self.select_shadingtype == "VERTEXCOLORS":
            self._draw_vertexcolors(context, box)

        row = layout.row()
        box = row.box()
        row = box.row()
        row.alert = self.select_blendtype == "None"
        row.prop(self, "select_blendtype", expand=True)
        row = box.row()
        row.prop(self, "select_alpha", text="Enable alpha transparency")
        self._draw_needs_mat_alpha_mode(
            context, box, self.select_blendtype, self.select_alpha
        )
        self._draw_needs_show_backface(
            context, box, self.select_blendtype, self.select_alpha
        )

        row = layout.row()
        box = row.box()
        row = box.row()
        row.prop(self, "select_teximage", text="")
        self._draw_needs_channel_packed(
            box, self.select_teximage, self.select_blendtype, self.select_alpha
        )
        row = box.row()
        row.prop(self, "select_texreflect", text="Reflective")

    def _draw_shadedcolors(self, box):
        row = box.row()
        row.prop(self, "select_diffusecolor", text="Diffuse")

        row = box.row()
        # Specular color and exponent, side by side
        row.prop(self, "select_specularcolor", text="Specular")
        column = row.column()
        column.prop(self, "select_specularexp", text="")

        row = box.row()
        row.prop(self, "select_basealpha", text="Base alpha", slider=True)

    def _draw_unshadedcolors(self, box):
        row = box.row()
        row.prop(self, "select_basecolor", text="Base color")

        row = box.row()
        row.prop(self, "select_basealpha", text="Base alpha", slider=True)

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
        needs_channel_packed = select_blendtype in ("INVMULTIPLY", "SUBTRACT") or (
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
        if context.scene.render.engine != "BLENDER_EEVEE":
            return
        needs_alphablend = select_alpha or select_blendtype in (
            "ADD",
            "INVMULTIPLY",
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

    def _draw_needs_show_backface(self, context, box, select_blendtype, select_alpha):
        if context.scene.render.engine != "BLENDER_EEVEE":
            return
        needs_show_backface = select_alpha or select_blendtype in (
            "ADD",
            "INVMULTIPLY",
            "SUBTRACT",
        )
        has_show_backface = context.active_object.active_material.show_transparent_back
        if needs_show_backface and not has_show_backface:
            row = box.row()
            row.alert = True
            row.label(text="Material should have Show Backface enabled")
            row = box.row()
            row.alert = True
            row.operator(
                "xeiosshader.enable_show_backface", text="Click here to fix this"
            )

    def copy(self, node: "XeiosShaderNode"):
        self.node_tree = node.node_tree.copy()

    def free(self):
        bpy.data.node_groups.remove(self.node_tree, do_unlink=True)
