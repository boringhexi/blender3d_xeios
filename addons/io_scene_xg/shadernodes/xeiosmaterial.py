import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
)
from bpy.types import Image, ShaderNodeCustomGroup


class XeiosMaterialShaderNode(ShaderNodeCustomGroup):
    bl_name = "XeiosMaterialShaderNode"
    bl_label = "Xeios Material"

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

    matcolormodes = [
        (
            "DIFFUSESPECULAR",
            "Diffuse + Specular",
            "Use material's diffuse and specular colors",
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
    select_matcolormode: EnumProperty(
        description="Material color mode",
        items=matcolormodes,
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

    def draw_buttons(self, context, layout):
        row = layout.row()
        row.alert = self.select_shadingtype == "None"
        row.prop(self, "select_shadingtype", text="")

        row = layout.row()
        row.alert = self.select_blendtype == "None"
        row.prop(self, "select_blendtype", text="")

        row = layout.row()
        row.alert = self.select_matcolormode == "None"
        row.prop(self, "select_matcolormode", text="")

        # Based on select_matcolormode, the next section will either be
        # diffuse + specular or vertex colors
        row = layout.row()
        row.column()  # indent the next section with an empty column
        if self.select_matcolormode == "DIFFUSESPECULAR":
            box = row.box()

            row = box.row()
            row.prop(self, "select_diffusecolor", text="Diffuse")

            row = box.row()
            # Specular color and exponent, side by side
            row.prop(self, "select_specularcolor", text="Specular")
            column = row.column()
            column.prop(self, "select_specularexp", text="")
            column.scale_x = 1.5  # a little wider to show the number more clearly

        elif self.select_matcolormode == "VERTEXCOLORS":
            column = row.column()
            ob = context.active_object
            if ob.type == "MESH" and ob.data.color_attributes:
                column.prop(self, "select_vertexcolors", text="", icon="GROUP_VCOL")
            else:
                column.alert = True
                column.label(text="No vertex colors")

        row = layout.row()
        row.prop(self, "select_teximage", text="")

        row = layout.row()
        row.column()  # indent the next section with an empty column
        col = row.column()
        col.prop(self, "select_texreflect", text="Reflective")

    def copy(self, node: "XeiosMaterialShaderNode"):
        self.node_tree = node.node_tree.copy()

    def free(self):
        bpy.data.node_groups.remove(self.node_tree, do_unlink=True)
