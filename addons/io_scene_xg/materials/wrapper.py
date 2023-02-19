# This file contains code from:
# https://projects.blender.org/blender/blender/src/branch/main/release/scripts/modules/bpy_extras/node_shader_utils.py

# Potential improvements:
# - combine MyShaderWrapper into MyShaderPrincipledBSDFWrapper, giving me a single class
# - slim down the texture stuff down to what it's meant to do: only texture linked to
#       Base Color on import, don't care about links at all on export
#   - this may involve folding that MyShaderImageTextureWrapper functionality into my
#       Principled wrapper
# - After combining classes, can make all node positioning relative to the output node
# - lazy initialization for Color Attribute node, allow the import of vertex colors to
#       be actually visible in the material output
#       - warning, this will require a Multiply Color setup with a texture that may or
#           may not be there
# - Better documentation explaining what nodes are searched for in readonly/export mode
#       and what nodes are created (and when) in writable/import mode.
#       (With better documentation, I can get rid of main() )
# - Maybe clear material's nodes before doing is_readonly=False creation. Maybe do away
#       with is_readonly and do like, import mode and export mode

import bpy
from mathutils import Color


def _set_check(func):
    from functools import wraps

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.is_readonly:
            assert not "Trying to set value to read-only shader!"
            return
        return func(self, *args, **kwargs)

    return wrapper


def rgb_to_rgba(rgb):
    return list(rgb) + [1.0]


def rgba_to_rgb(rgba):
    return Color((rgba[0], rgba[1], rgba[2]))


# All clamping value shall follow Blender's defined min/max (check relevant node definition .c file).
def values_clamp(val, minv, maxv):
    if hasattr(val, "__iter__"):
        return tuple(max(minv, min(maxv, v)) for v in val)
    else:
        return max(minv, min(maxv, val))


def node_search_by_type(starting_node, node_type):
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


class MyShaderWrapper:
    """
    Base class with minimal common ground for all types of shader interfaces we may want/need to implement.

    Difference from bpy's ShaderWrapper: location of the texcoord node on the grid
    """

    # The two mandatory nodes any children class should support.
    NODES_LIST = (
        "node_out",
        "_node_texcoords",
    )

    __slots__ = (
        "is_readonly",
        "material",
        "_textures",
        "_grid_locations",
        *NODES_LIST,
    )

    _col_size = 300
    _row_size = 300

    def _grid_to_location(self, x, y, dst_node=None, ref_node=None):
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

    def __init__(self, material, is_readonly=True, use_nodes=True):
        self.is_readonly = is_readonly
        self.material = material
        if not is_readonly:
            self.use_nodes = use_nodes
        self.update()

    def update(self):  # Should be re-implemented by children classes...
        for node in self.NODES_LIST:
            setattr(self, node, None)
        self._textures = {}
        self._grid_locations = set()

    def use_nodes_get(self):
        return self.material.use_nodes

    @_set_check
    def use_nodes_set(self, val):
        self.material.use_nodes = val
        self.update()

    use_nodes = property(use_nodes_get, use_nodes_set)

    def node_texcoords_get(self):
        if not self.use_nodes:
            return None
        if self._node_texcoords is ...:
            # Running only once, trying to find a valid texcoords node.
            for n in self.material.node_tree.nodes:
                if n.bl_idname == "ShaderNodeTexCoord":
                    self._node_texcoords = n
                    self._grid_to_location(0, 0, ref_node=n)
                    break
            if self._node_texcoords is ...:
                self._node_texcoords = None
        if self._node_texcoords is None and not self.is_readonly:
            tree = self.material.node_tree
            nodes = tree.nodes
            # links = tree.links

            node_texcoords = nodes.new(type="ShaderNodeTexCoord")
            node_texcoords.label = "Texture Coords"
            self._grid_to_location(-1, 1, dst_node=node_texcoords)
            self._node_texcoords = node_texcoords
        return self._node_texcoords

    node_texcoords = property(node_texcoords_get)


class MyPrincipledBSDFWrapper(MyShaderWrapper):
    """
    Hard coded shader setup, based in Principled BSDF. Adjusted for use with Xeios
    materials. Should cover most common cases on import, and gives a basic nodal shaders
    support for export.

    Differences from original PrincipledBSDFWrapper:
      - wrapping existing nodes is less strict, nodes don't have to be connected in such
        an exact way.
      - searches for and exposes more node types

    Supports basic diffuse/spec/, transparency, textures, vertex colors, and texture
        coordinate mode.

    Supported usage: Generally speaking, it works within its intended usage and may have
        strange results outside of that.
        - wrap a blank node material with is_readonly=False. This creates a new nodetree
            which can then be populated with imported values
                - wrapping an already-populated nodetree may have strange results.
        - wrap an existing node material with is_readonly=True. This searches for nodes
            and exposes the nodes it finds, whose values can then be exported.
            - wrapping an existing nodetree and attempting to access multiple textures
                may have strange results. x.node_imagetexture_existing, the first
                 texture found by the node search, is considered the "canon" texture.
    """

    NODES_LIST = (
        "node_out",
        "node_principled_bsdf",
        "node_diffuse_bsdf",
        "node_color_attribute",
        "node_imagetexture_existing",
    )

    __slots__ = (
        "is_readonly",
        "material",
        *NODES_LIST,
    )

    NODES_LIST = MyShaderWrapper.NODES_LIST + NODES_LIST

    def __init__(self, material, is_readonly=True, use_nodes=True):
        super(MyPrincipledBSDFWrapper, self).__init__(material, is_readonly, use_nodes)

    def update(self):
        super(MyPrincipledBSDFWrapper, self).update()

        if not self.use_nodes:
            return

        tree = self.material.node_tree

        nodes = tree.nodes
        links = tree.links

        # --------------------------------------------------------------------
        # Wrap existing nodes.

        # Get the active (or failing that, any) Material Output node
        renderer = bpy.context.scene.render.engine
        node_out = (
            tree.get_output_node(renderer) if renderer in ("EEVEE", "CYCLES") else None
        )
        if node_out is None:
            node_out = tree.get_output_node("ALL")
        if node_out is None:
            for n in nodes:
                if n.bl_idname == "ShaderNodeOutputMaterial" and n.inputs[0].is_linked:
                    node_out = n
                    break
        # starting from Material Output, for Principled BSDF node
        node_principled_bsdf = node_diffuse_bsdf = None
        if node_out is not None:
            node_principled_bsdf = node_search_by_type(
                node_out, "ShaderNodeBsdfPrincipled"
            )
            # (And also Diffuse BSDF node, though it will be prioritized lower)
            node_diffuse_bsdf = node_search_by_type(node_out, "ShaderNodeBsdfDiffuse")
        # from there get the Image Texture
        node_imagetexture_existing = None
        if node_principled_bsdf is not None:
            node_imagetexture_existing = node_search_by_type(
                node_principled_bsdf, "ShaderNodeTexImage"
            )
        elif node_diffuse_bsdf is not None:
            node_imagetexture_existing = node_search_by_type(
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
            self._grid_to_location(2, 1, dst_node=node_out)
        self.node_out = node_out

        if node_principled_bsdf is not None:
            # store in internal list of known occupied grid locations
            self._grid_to_location(0, 0, ref_node=node_principled_bsdf)
        elif not self.is_readonly:
            # create new & move to an unoccupied grid location
            node_principled_bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")
            node_principled_bsdf.label = "Principled BSDF"
            self._grid_to_location(1, 1, dst_node=node_principled_bsdf)
            # Link
            links.new(
                node_principled_bsdf.outputs["BSDF"], self.node_out.inputs["Surface"]
            )
        self.node_principled_bsdf = node_principled_bsdf

        # We won't create an Image Texture until it is first accessed, because some
        # imported models may not have a texture

        self.node_diffuse_bsdf = node_diffuse_bsdf
        self.node_color_attribute = node_color_attribute
        self.node_imagetexture_existing = node_imagetexture_existing
        self._node_texcoords = ...  # lazy initialization

    # --------------------------------------------------------------------
    # Base Color.

    def base_color_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return self.material.diffuse_color
        return rgba_to_rgb(self.node_principled_bsdf.inputs["Base Color"].default_value)

    @_set_check
    def base_color_set(self, color):
        color = values_clamp(color, 0.0, 1.0)
        color = rgb_to_rgba(color)
        self.material.diffuse_color = color
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Base Color"].default_value = color

    base_color = property(base_color_get, base_color_set)

    def base_color_texture_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return None
        return MyShaderImageTextureWrapper(
            self,
            self.node_principled_bsdf,
            self.node_principled_bsdf.inputs["Base Color"],
            grid_row_diff=0,
        )

    base_color_texture = property(base_color_texture_get)

    # --------------------------------------------------------------------
    # Specular.

    def specular_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return self.material.specular_intensity
        return self.node_principled_bsdf.inputs["Specular"].default_value

    @_set_check
    def specular_set(self, value):
        value = values_clamp(value, 0.0, 1.0)
        self.material.specular_intensity = value
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Specular"].default_value = value

    specular = property(specular_get, specular_set)

    def specular_tint_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return 0.0
        return self.node_principled_bsdf.inputs["Specular Tint"].default_value

    @_set_check
    def specular_tint_set(self, value):
        value = values_clamp(value, 0.0, 1.0)
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Specular Tint"].default_value = value

    specular_tint = property(specular_tint_get, specular_tint_set)

    # --------------------------------------------------------------------
    # Roughness (also sort of inverse of specular hardness...).

    def roughness_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return self.material.roughness
        return self.node_principled_bsdf.inputs["Roughness"].default_value

    @_set_check
    def roughness_set(self, value):
        value = values_clamp(value, 0.0, 1.0)
        self.material.roughness = value
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Roughness"].default_value = value

    roughness = property(roughness_get, roughness_set)

    # --------------------------------------------------------------------
    # Transparency settings.

    def alpha_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return 1.0
        return self.node_principled_bsdf.inputs["Alpha"].default_value

    @_set_check
    def alpha_set(self, value):
        value = values_clamp(value, 0.0, 1.0)
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Alpha"].default_value = value

    alpha = property(alpha_get, alpha_set)

    # Will only be used as gray-scale one...
    def alpha_texture_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return None
        return MyShaderImageTextureWrapper(
            self,
            self.node_principled_bsdf,
            self.node_principled_bsdf.inputs["Alpha"],
            use_alpha=True,
            grid_row_diff=-1,
            colorspace_name="Non-Color",
        )

    alpha_texture = property(alpha_texture_get)

    # --------------------------------------------------------------------
    # Emission color.

    def emission_color_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return Color((0.0, 0.0, 0.0))
        return rgba_to_rgb(self.node_principled_bsdf.inputs["Emission"].default_value)

    @_set_check
    def emission_color_set(self, color):
        if self.use_nodes and self.node_principled_bsdf is not None:
            color = values_clamp(color, 0.0, 1000000.0)
            color = rgb_to_rgba(color)
            self.node_principled_bsdf.inputs["Emission"].default_value = color

    emission_color = property(emission_color_get, emission_color_set)

    def emission_color_texture_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return None
        return MyShaderImageTextureWrapper(
            self,
            self.node_principled_bsdf,
            self.node_principled_bsdf.inputs["Emission"],
            grid_row_diff=0,
        )

    emission_color_texture = property(emission_color_texture_get)

    def emission_strength_get(self):
        if not self.use_nodes or self.node_principled_bsdf is None:
            return 1.0
        return self.node_principled_bsdf.inputs["Emission Strength"].default_value

    @_set_check
    def emission_strength_set(self, value):
        value = values_clamp(value, 0.0, 1000000.0)
        if self.use_nodes and self.node_principled_bsdf is not None:
            self.node_principled_bsdf.inputs["Emission Strength"].default_value = value

    emission_strength = property(emission_strength_get, emission_strength_set)


class MyShaderImageTextureWrapper:
    """
    Generic 'image texture'-like wrapper, handling image node and texture coordinates.

    Differs from Blender's ShaderImageTextureWrapper in that it can be initialized with
    an arbitrary ShaderNodeTexImage. As such, the Image Texture node doesn't need to be
    directly connected to the Principled BSDF node.
    """

    # Note: this class assumes we are using nodes, otherwise it should never be used...

    NODES_LIST = (
        "node_dst",
        "socket_dst",
        "_node_image",
    )

    __slots__ = (
        "owner_shader",
        "is_readonly",
        "grid_row_diff",
        "use_alpha",
        "colorspace_is_data",
        "colorspace_name",
        *NODES_LIST,
    )

    def __new__(
        cls,
        owner_shader: MyPrincipledBSDFWrapper,
        node_dst,
        socket_dst,
        *_args,
        **_kwargs
    ):
        # If owner_shader already has a wrapped texture with this src/dst, return that
        instance = owner_shader._textures.get((node_dst, socket_dst), None)
        if instance is not None:
            return instance
        instance = super(MyShaderImageTextureWrapper, cls).__new__(cls)
        owner_shader._textures[(node_dst, socket_dst)] = instance
        return instance

    def __init__(
        self,
        owner_shader: MyPrincipledBSDFWrapper,
        node_dst,
        socket_dst,
        existing_texnode=None,
        grid_row_diff=0,
        use_alpha=False,
        colorspace_is_data=...,
        colorspace_name=...,
    ):
        self.owner_shader = owner_shader
        self.is_readonly = owner_shader.is_readonly
        self.node_dst = node_dst
        self.socket_dst = socket_dst
        self.grid_row_diff = grid_row_diff
        self.use_alpha = use_alpha
        self.colorspace_is_data = colorspace_is_data
        self.colorspace_name = colorspace_name

        self._node_image = ...

        # tree = node_dst.id_data
        # nodes = tree.nodes
        # links = tree.links

        if owner_shader.node_imagetexture_existing is not None:
            self._node_image = owner_shader.node_imagetexture_existing
        elif socket_dst.is_linked:
            # grab and store texture node connected to the provided socket_dst
            from_node = socket_dst.links[0].from_node
            if from_node.bl_idname == "ShaderNodeTexImage":
                self._node_image = from_node

        if self.node_image is not None:
            # grab and store the texture node's texture coordinate node if one exists
            socket_dst = self.node_image.inputs["Vector"]
            if socket_dst.is_linked:
                from_node = socket_dst.links[0].from_node
                if from_node.bl_idname == "ShaderNodeMapping":
                    self._node_mapping = from_node

    # --------------------------------------------------------------------
    # Image.

    def node_image_get(self):
        if self._node_image is ...:
            # Running only once, trying to find a valid image node.
            if self.socket_dst.is_linked:
                node_image = self.socket_dst.links[0].from_node
                if node_image.bl_idname == "ShaderNodeTexImage":
                    self._node_image = node_image
                    self.owner_shader._grid_to_location(0, 0, ref_node=node_image)
            if self._node_image is ...:
                self._node_image = None
        if self._node_image is None and not self.is_readonly:
            tree = self.owner_shader.material.node_tree

            node_image = tree.nodes.new(type="ShaderNodeTexImage")
            self.owner_shader._grid_to_location(
                -1,
                0 + self.grid_row_diff,
                dst_node=node_image,
                ref_node=self.node_dst,
            )

            tree.links.new(
                node_image.outputs["Alpha" if self.use_alpha else "Color"],
                self.socket_dst,
            )
            if self.use_alpha:
                self.owner_shader.material.blend_method = "BLEND"

            self._node_image = node_image
        return self._node_image

    node_image = property(node_image_get)

    def image_get(self):
        return self.node_image.image if self.node_image is not None else None

    @_set_check
    def image_set(self, image):
        if self.colorspace_is_data is not ...:
            if (
                image.colorspace_settings.is_data != self.colorspace_is_data
                and image.users >= 1
            ):
                image = image.copy()
            image.colorspace_settings.is_data = self.colorspace_is_data
        if self.colorspace_name is not ...:
            if (
                image.colorspace_settings.name != self.colorspace_name
                and image.users >= 1
            ):
                image = image.copy()
            image.colorspace_settings.name = self.colorspace_name
        if self.use_alpha:
            # Try to be smart, and only use image's alpha output if image actually has alpha data.
            tree = self.owner_shader.material.node_tree
            if image.channels < 4 or image.depth in {24, 8}:
                tree.links.new(self.node_image.outputs["Color"], self.socket_dst)
            else:
                tree.links.new(self.node_image.outputs["Alpha"], self.socket_dst)
        self.node_image.image = image

    image = property(image_get, image_set)

    def projection_get(self):
        return self.node_image.projection if self.node_image is not None else "FLAT"

    @_set_check
    def projection_set(self, projection):
        self.node_image.projection = projection

    projection = property(projection_get, projection_set)

    def texcoords_get(self):
        if self.node_image is not None:
            socket = self.node_image.inputs["Vector"]
            if socket.is_linked:
                return socket.links[0].from_socket.name
        return "UV"

    @_set_check
    def texcoords_set(self, texcoords):
        # Image texture node already defaults to UVs, no extra node needed.
        if texcoords == "UV":
            return
        tree = self.node_image.id_data
        links = tree.links
        node_dst = self.node_image
        socket_src = self.owner_shader.node_texcoords.outputs[texcoords]
        links.new(socket_src, node_dst.inputs["Vector"])

    texcoords = property(texcoords_get, texcoords_set)

    def extension_get(self):
        return self.node_image.extension if self.node_image is not None else "REPEAT"

    @_set_check
    def extension_set(self, extension):
        self.node_image.extension = extension

    extension = property(extension_get, extension_set)


def main():
    import bpy

    # # Example: Create a new writable nodetree, to be populated with imported values
    # ma = bpy.context.active_object.active_material
    # ma_wrap = MyPrincipledBSDFWrapper(ma, is_readonly=False)
    # # Access a texture to create the texture node
    # tx_wrap = ma_wrap.base_color_texture
    # # Assign a texcoord mode to create the texture coordinate node
    # tx_wrap.texcoords = "Reflection"

    # Example: Wrap around an existing read-only nodetree, exposes values to be exported
    ma = bpy.context.active_object.active_material
    ma_wrap = MyPrincipledBSDFWrapper(ma, is_readonly=True)
    print("===================")
    print("Principled BSDF node: ", ma_wrap.node_principled_bsdf)
    print("Diffuse BSDF node: ", ma_wrap.node_diffuse_bsdf)
    print("Color Attribute node: ", ma_wrap.node_color_attribute)
    print("Image Texture node: ", ma_wrap.node_imagetexture_existing)
    print("Texcoord node: ", ma_wrap.node_texcoords)


if __name__ == "__main__":
    main()
