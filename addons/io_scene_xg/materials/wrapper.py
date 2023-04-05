# This file contains code from:
# https://projects.blender.org/blender/blender/src/branch/main/release/scripts/modules/bpy_extras/node_shader_utils.py
# https://projects.blender.org/blender/blender-addons/src/branch/main/io_scene_obj/import_obj.py
# https://projects.blender.org/blender/blender-addons/src/branch/main/io_scene_obj/export_obj.py

from math import sqrt
from typing import Callable, Iterable, List, Optional, Sequence
from typing import SupportsFloat as Numeric
from typing import Tuple, Union

import bpy
from bpy.types import (
    Image,
    Material,
    Mesh,
    Node,
    ShaderNodeOutputMaterial,
    ShaderNodeTexImage,
    ShaderNodeTree,
)
from mathutils import Color

from ..xg.xgscene import Constants, XgMaterial


def _set_check(func: Callable) -> Callable:
    from functools import wraps

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.is_readonly:
            assert not "Trying to set value to read-only shader!"
            return
        return func(self, *args, **kwargs)

    return wrapper


def rgb_to_rgba(rgb: Sequence[Numeric]) -> List[Numeric]:
    return list(rgb) + [1.0]


def rgba_to_rgb(rgba: Sequence) -> Color:
    return Color((rgba[0], rgba[1], rgba[2]))


# All clamping value shall follow Blender's defined min/max
# (check relevant node definition .c file).
def values_clamp(
    val: Union[Numeric, Iterable], minv: Numeric, maxv: Numeric
) -> Union[Numeric, Tuple[Numeric]]:
    """clamp a value or an iterable of values to between [minv,maxv] inclusive"""
    if hasattr(val, "__iter__"):
        return tuple(max(minv, min(maxv, v)) for v in val)
    else:
        return max(minv, min(maxv, val))


def node_search_by_type(starting_node: Node, node_type: str) -> Optional[Node]:
    """search breadth-first through inputs starting from the chosen Blender node

    :param starting_node: node from which to start searching inputs
    :param node_type: name of the node type (bl_idname)
    :return: Blender node instance
    """
    ret = None
    queue = [starting_node]
    while queue:
        n = queue.pop()
        for input_socket in n.inputs:
            if ret is not None:
                break
            if not input_socket.is_linked:
                continue
            for input_link in input_socket.links:
                input_node = input_link.from_node
                if input_node.bl_idname == node_type:
                    ret = input_node
                    break
                else:
                    queue.append(input_node)
    return ret


def get_material_output_node(material: Material) -> Optional[ShaderNodeOutputMaterial]:
    """Get the active (or failing that, any) Material Output node"""
    renderer = bpy.context.scene.render.engine
    tree: ShaderNodeTree = material.node_tree
    node_out = (
        tree.get_output_node(renderer) if renderer in ("EEVEE", "CYCLES") else None
    )
    if node_out is None:
        node_out = tree.get_output_node("ALL")
    if node_out is None:
        for n in tree.nodes:
            if n.bl_idname == "ShaderNodeOutputMaterial" and n.inputs[0].is_linked:
                node_out = n
                break
    return node_out


def xgspecular_to_roughness(xg_specular: float) -> float:
    # based on io_scene_obj's way, not sure how accurate to Xeios/XG
    xg_specular = values_clamp(xg_specular, 0, 1000)
    roughness = 1.0 - (sqrt(xg_specular / 1000))
    return roughness


def roughness_to_xgspecular(roughness: float) -> float:
    # based on io_scene_obj's way, not sure how accurate to Xeios/XG
    spec = 1.0 - roughness
    spec *= spec * 1000
    return spec


def xg_shadingtype(wrapper: "MyPrincipledBSDFWrapper", mesh: Mesh) -> str:
    """return the XG/Xeios shading type corresponding to material and mesh properties

    :param wrapper: MyPrincipledBSDFWrapper instance
    :param mesh: Blender Mesh the MyPrincipledBSDFWrapper material is being applied to
    :return: "VERTEXCOLORS" or "UNSHADED" or "SHADED"
    """
    if mesh.color_attributes.active:
        return "VERTEXCOLORS"

    if not wrapper.use_nodes:
        return "SHADED"

    if not (wrapper.node_principled_bsdf or wrapper.node_diffuse_bsdf):
        return "UNSHADED"
    if (
        wrapper.node_principled_bsdf
        and not wrapper.node_principled_bsdf.inputs["Base Color"].is_linked
        and wrapper.node_principled_bsdf.inputs["Emission"].is_linked
    ):
        return "UNSHADED"

    return "SHADED"


def material_uses_alpha(material: Material) -> bool:
    """Returns whether a Blender material can be reasonably assumed to use alpha

    Intended use: Before using MyPrincipledBSDFWrapper to wrap an already-populated
    material (i.e. when exporting), this function should be used to check if the
    material uses alpha, so that the proper `use_alpha` can be passed to the
    MyPrincipledBSDFWrapper constructor.

    :param material: a Blender Material
    :return: True if material can be reasonably assumed to use alpha, False otherwise
    """
    # non-Node materials don't support alpha
    if not material.use_nodes:
        return False

    node_out = get_material_output_node(material)

    # True if the Principled BSDF node is using an Alpha input or < 1.0 Alpha value
    principled_bsdf = node_search_by_type(node_out, "ShaderNodeBsdfPrincipled")
    if (
        principled_bsdf
        and not principled_bsdf.inputs["Alpha"].is_linked
        and principled_bsdf.inputs["Alpha"].default_value < 1.0
    ):
        return True

    # Assume True if the image texture has an alpha channel and its Alpha output is used
    # (Potential improvement: check if Alpha output can be reached from Material Output)
    image_texture = node_search_by_type(node_out, "ShaderNodeTexImage")
    image = image_texture.image
    if (
        image_texture
        and image_texture.outputs["Alpha"].is_linked
        and image
        and not (image.channels < 4 or image.depth in {8, 24})
        and image.alpha_mode != "NONE"
    ):
        return True
    return False


def xgmaterial_uses_alpha(xgmaterial: XgMaterial) -> bool:
    """Returns whether a xgMaterial node uses alpha

    Intended use: Before using MyPrincipledBSDFWrapper to wrap a blank material (i.e.
    when importing), this function should be used to check if the xgMaterial being
    imported from uses alpha, so that the proper `use_alpha` can be passed to the
    MyPrincipledBSDFWrapper constructor.

    :param xgmaterial: a XgMaterial node
    :return: True if xgmaterial uses alpha, False otherwise
    """
    if xgmaterial.blendType in (Constants.BlendType.ADD, Constants.BlendType.MIXALPHA):
        return True
    if (xgmaterial.flags & Constants.Flags.USEALPHA) and xgmaterial.blendType in (
        Constants.BlendType.MIX,
        Constants.BlendType.UNKNOWN,
    ):
        return True
    return False


class MyPrincipledBSDFWrapper:
    """
    Hard coded shader setup, based in Principled BSDF. Adjusted for use with Xeios
    materials. Should cover most common cases on import, and gives a basic nodal shaders
    support for export.

    Supported usage:
    - Wrap a new/blank node material with is_readonly=False. This creates a new nodetree
        which can then be populated with imported values.
            - Exposed nodes include material output and principled BSDF nodes, with the
                dynamic on-access creation of image texture node. Can also access and
                change base color, alpha, specular, roughness, texcoord mode, and image.
    - wrap an existing node material with is_readonly=True. This searches for nodes
        and exposes the nodes it finds, whose values can then be exported.
            - Exposed nodes include material output, principled BSDF, diffuse BSDF,
                color attribute, image texture. Can also access base color, alpha,
                specular, roughness, texcoord mode, and image.
    """

    NODES_LIST = (
        "node_out",
        "node_principled_bsdf",
        "node_diffuse_bsdf",
        "node_color_attribute",
        "_node_image_texture",
        "_node_texcoords",
    )

    __slots__ = (
        "is_readonly",
        "use_alpha",
        "material",
        "_grid_locations",
        *NODES_LIST,
    )

    _col_size = 300
    _row_size = 300

    def _grid_to_location(
        self,
        x: int,
        y: int,
        dst_node: Optional[Node] = None,
        ref_node: Optional[Node] = None,
    ) -> Tuple:
        """convert grid coordinates to a location in the shader editor

        :param x: grid coordinate x (based on self._col_size)
        :param y: grid coordinate y (based on self._row_size)
        :param dst_node: node to move to the calculated location, if provided
        :param ref_node: calculate location relative to this node
        :return: (x,y) location, to be used in the shader editor
        """
        if ref_node is not None:  # x and y are relative to this node location.
            nx = round(ref_node.location.x / self._col_size)
            ny = round(ref_node.location.y / self._row_size)
            x += nx
            y += ny
        loc = None
        while True:
            loc = (x * self._col_size, y * self._row_size)
            if loc not in self._grid_locations:
                break
            loc = (x * self._col_size, (y - 1) * self._row_size)
            if loc not in self._grid_locations:
                break
            loc = (x * self._col_size, (y - 2) * self._row_size)
            if loc not in self._grid_locations:
                break
            x -= 1
        self._grid_locations.add(loc)
        if dst_node is not None:
            dst_node.location = loc
            dst_node.width = min(dst_node.width, self._col_size - 20)
        return loc

    def __init__(
        self,
        material: Material,
        is_readonly: bool = True,
        use_nodes: bool = True,
        use_alpha: bool = False,
    ) -> None:
        self.is_readonly = is_readonly
        self.material = material
        self.use_alpha = use_alpha
        if not is_readonly:
            self.use_nodes = use_nodes
        self.update()

    def update(self) -> None:
        for node in self.NODES_LIST:
            setattr(self, node, None)
        self._grid_locations = set()

        if not self.use_nodes:
            return

        tree: ShaderNodeTree = self.material.node_tree

        nodes = tree.nodes
        links = tree.links

        # --------------------------------------------------------------------
        # Wrap existing nodes.

        node_out = get_material_output_node(self.material)
        # starting from Material Output, search for the Principled BSDF node
        node_principled_bsdf = node_diffuse_bsdf = None
        if node_out is not None:
            node_principled_bsdf = node_search_by_type(
                node_out, "ShaderNodeBsdfPrincipled"
            )
            # (And also Diffuse BSDF node, though it will be prioritized lower)
            node_diffuse_bsdf = node_search_by_type(node_out, "ShaderNodeBsdfDiffuse")
        # from there, search for the Image Texture
        node_image_texture = None
        if node_principled_bsdf is not None:
            node_image_texture = node_search_by_type(
                node_principled_bsdf, "ShaderNodeTexImage"
            )
        elif node_diffuse_bsdf is not None:
            node_image_texture = node_search_by_type(
                node_diffuse_bsdf, "ShaderNodeTexImage"
            )
        # And the Color Attribute (vertex colors) in case we need it
        node_color_attribute = None
        if node_principled_bsdf is not None:
            node_color_attribute = node_search_by_type(
                node_principled_bsdf, "ShaderNodeVertexColor"
            )
        elif node_diffuse_bsdf is not None:
            node_color_attribute = node_search_by_type(
                node_diffuse_bsdf, "ShaderNodeVertexColor"
            )

        # --------------------------------------------------------------------
        # If the material is writeable, create nodes that don't exist yet
        if node_out is not None:
            # store in internal list of known occupied grid locations
            self._grid_to_location(0, 0, ref_node=node_out)
        elif not self.is_readonly:
            # create new & move to an unoccupied grid location
            node_out = nodes.new(type="ShaderNodeOutputMaterial")
            node_out.label = "Material Out"
            node_out.target = "ALL"
            self._grid_to_location(1, 1, dst_node=node_out)
        self.node_out = node_out

        if node_principled_bsdf is not None:
            # store in internal list of known occupied grid locations
            self._grid_to_location(0, 0, ref_node=node_principled_bsdf)
        elif not self.is_readonly:
            # create new & move to an unoccupied grid location
            node_principled_bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
            node_principled_bsdf.label = "Principled BSDF"
            self._grid_to_location(
                -1, 0, dst_node=node_principled_bsdf, ref_node=node_out
            )
            # Link
            links.new(
                node_principled_bsdf.outputs["BSDF"], self.node_out.inputs["Surface"]
            )

        self.node_principled_bsdf = node_principled_bsdf
        self.node_diffuse_bsdf = node_diffuse_bsdf
        self.node_color_attribute = node_color_attribute
        if node_image_texture is not None:
            self._node_image_texture = node_image_texture
        else:
            self._node_image_texture = ...  # lazy initialization
        self._node_texcoords = ...

    @property
    def use_nodes(self) -> bool:
        return self.material.use_nodes

    @use_nodes.setter
    @_set_check
    def use_nodes(self, val) -> None:
        self.material.use_nodes = val
        self.update()

    @property
    def node_image_texture(self) -> Optional[ShaderNodeTexImage]:
        if not self.use_nodes:
            return None
        if self._node_image_texture is ...:
            # Running only once, trying to find a valid image texture node.
            found = node_search_by_type(self.node_principled_bsdf, "ShaderNodeTexImage")
            if found is not None:
                self._grid_to_location(0, 0, ref_node=found)
                self._node_image_texture = found

        if self._node_image_texture is ...:
            if self.is_readonly:
                self._node_image_texture = None
            else:
                # Create new Image Texture ...
                tree = self.material.node_tree
                node_image_texture = tree.nodes.new(type="ShaderNodeTexImage")
                self._grid_to_location(
                    -1,
                    0,
                    dst_node=node_image_texture,
                    ref_node=self.node_principled_bsdf,
                )
                # ... and link to Principled BSDF
                tree.links.new(
                    node_image_texture.outputs["Color"],
                    self.node_principled_bsdf.inputs["Base Color"],
                )
                self._link_texalpha_or_floatalpha()

                self._node_image_texture = node_image_texture

        return self._node_image_texture

    @property
    def image(self) -> Optional[Image]:
        if not self.use_nodes:
            return None
        return (
            self.node_image_texture.image
            if self.node_image_texture is not None
            else None
        )

    @image.setter
    @_set_check
    def image(self, image: Image) -> None:
        if self.use_nodes:
            # node_image_texture gets automatically created
            self.node_image_texture.image = image

    @property
    def texprojection(self) -> str:
        if not self.use_nodes:
            return "FLAT"
        return self.node_image_texture.projection

    @texprojection.setter
    @_set_check
    def texprojection(self, projection: str) -> None:
        if self.use_nodes:
            self.node_image_texture.projection = projection

    @property
    def texcoords(self) -> str:
        if not self.use_nodes:
            return "UV"

        if self._node_texcoords is ...:
            if self._node_image_texture in (None, ...):
                self._node_texcoords = None
            else:
                found = node_search_by_type(
                    self._node_image_texture, "ShaderNodeTexCoord"
                )
                if found is not None:
                    self._grid_to_location(0, 0, ref_node=found)
                    self._node_texcoords = found

        self._create_node_texcoords()

        if self.node_image_texture is not None:
            socket = self.node_image_texture.inputs["Vector"]
            if socket.is_linked:
                return socket.links[0].from_socket.name
        return "UV"

    def _create_node_texcoords(self):
        if self._node_texcoords is ... and not self.is_readonly:
            # Create new Texture Coordinates node...
            tree = self.material.node_tree
            node_texcoords = tree.nodes.new(type="ShaderNodeTexCoord")
            node_texcoords.label = "Texture Coordinates"
            self._grid_to_location(
                -1, 0, dst_node=node_texcoords, ref_node=self.node_image_texture
            )
            # ... and link it to the (automatically created) image texture node
            socket_dst = self.node_image_texture.inputs["Vector"]
            socket_src = node_texcoords.outputs["UV"]
            tree.links.new(socket_src, socket_dst)
            self._node_texcoords = node_texcoords

    @texcoords.setter
    @_set_check
    def texcoords(self, texcoords: str) -> None:
        # Image texture node already defaults to UVs, no extra node needed.
        if texcoords == "UV":
            return
        if self.use_nodes:
            self._create_node_texcoords()
            tree = self.material.node_tree
            node_dst = self.node_image_texture
            socket_src = self._node_texcoords.outputs[texcoords]
            tree.links.new(socket_src, node_dst.inputs["Vector"])

    # --------------------------------------------------------------------
    # Base Color.

    @property
    def base_color(self) -> Sequence[float]:
        if not self.use_nodes or self.node_principled_bsdf is None:
            return self.material.diffuse_color
        return rgba_to_rgb(self.node_principled_bsdf.inputs["Base Color"].default_value)

    @base_color.setter
    @_set_check
    def base_color(self, color: Sequence[float]) -> None:
        color = values_clamp(color, 0.0, 1.0)
        color = rgb_to_rgba(color)
        self.material.diffuse_color = color
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Base Color"].default_value = color

    # --------------------------------------------------------------------
    # Specular.

    @property
    def specular(self) -> float:
        if not self.use_nodes or self.node_principled_bsdf is None:
            return self.material.specular_intensity
        return self.node_principled_bsdf.inputs["Specular"].default_value

    @specular.setter
    @_set_check
    def specular(self, value: float) -> None:
        value = values_clamp(value, 0.0, 1.0)
        self.material.specular_intensity = value
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Specular"].default_value = value

    # --------------------------------------------------------------------
    # Roughness (also sort of inverse of specular hardness...).

    @property
    def roughness(self) -> float:
        if not self.use_nodes or self.node_principled_bsdf is None:
            return self.material.roughness
        return self.node_principled_bsdf.inputs["Roughness"].default_value

    @roughness.setter
    @_set_check
    def roughness(self, value: float) -> None:
        value = values_clamp(value, 0.0, 1.0)
        self.material.roughness = value
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Roughness"].default_value = value

    # --------------------------------------------------------------------
    # Transparency settings.

    @property
    def alpha(self) -> float:
        if not self.use_nodes or self.node_principled_bsdf is None:
            return 1.0
        return self.node_principled_bsdf.inputs["Alpha"].default_value

    @alpha.setter
    @_set_check
    def alpha(self, value: float) -> None:
        value = values_clamp(value, 0.0, 1.0)
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Alpha"].default_value = value
            self._link_texalpha_or_floatalpha()

    def _link_texalpha_or_floatalpha(self):
        """Choose between using the texture's alpha channel or a constant alpha value

        Determine which to use (favoring constant alpha value if it's < 1.0), then set
        up or delete the necessary node link.
        (Xeios can do both alpha types at once, but our simple Blender node setup can't)
        """
        tree = self.material.node_tree
        if self.use_alpha and self.alpha == 1.0:
            # create link if it doesn't exist
            if not self.node_principled_bsdf.inputs["Alpha"].is_linked:
                tree.links.new(
                    self.node_image_texture.outputs["Alpha"],
                    self.node_principled_bsdf.inputs["Alpha"],
                )
        else:
            # remove link
            alphalinks = self.node_principled_bsdf.inputs["Alpha"].links
            for link in alphalinks:
                alphalinks.remove(link)

    # --------------------------------------------------------------------
    # Other material settings.

    @property
    def use_backface_culling(self) -> bool:
        return self.material.use_backface_culling

    @use_backface_culling.setter
    @_set_check
    def use_backface_culling(self, val: bool) -> None:
        self.material.use_backface_culling = val

    @property
    def use_eevee_alpha_blend(self) -> bool:
        return self.material.blend_method == "BLEND"

    @use_eevee_alpha_blend.setter
    @_set_check
    def use_eevee_alpha_blend(self, val: bool) -> None:
        if val:
            self.material.blend_method = "BLEND"
        else:
            self.material.blend_method = "OPAQUE"


def main():
    import bpy

    # # Example: Create a new writable nodetree, to be populated with imported values
    # ma = bpy.context.active_object.active_material
    # ma_wrap = MyPrincipledBSDFWrapper(ma, is_readonly=False)
    # # Access a texture to create the texture node
    # texture = ma_wrap.node_image_texture
    # # Assign a texcoord mode to create the texture coordinate node
    # ma_wrap.texcoords = "Reflection"
    # Example: Wrap around an existing read-only nodetree, exposes values to be exported
    ma = bpy.context.active_object.active_material
    ma_wrap = MyPrincipledBSDFWrapper(ma, is_readonly=True)
    print("===================")
    print("Principled BSDF node: ", ma_wrap.node_principled_bsdf)
    print("Diffuse BSDF node: ", ma_wrap.node_diffuse_bsdf)
    print("Color Attribute node: ", ma_wrap.node_color_attribute)
    print("Image Texture node: ", ma_wrap.node_image_texture)
    print("Texcoords mode: ", ma_wrap.texcoords)


if __name__ == "__main__":
    main()
