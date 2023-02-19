# This file contains code from:
# https://projects.blender.org/blender/blender/src/branch/main/release/scripts/modules/bpy_extras/node_shader_utils.py

# Potential improvements:
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


class MyPrincipledBSDFWrapper:
    """
    Hard coded shader setup, based in Principled BSDF. Adjusted for use with Xeios
    materials. Should cover most common cases on import, and gives a basic nodal shaders
    support for export.

    Differences from original PrincipledBSDFWrapper:
      - wrapping existing nodes is less strict, nodes don't have to be connected in such
        an exact way.
      - searches for and exposes more node types
      - different placement of nodes on the grid

    Supports basic diffuse/spec/, transparency, textures, vertex colors, and texture
        coordinate mode.

    Supported usage: Generally speaking, it works within its intended usage and may have
        strange results outside of that.
        - wrap a blank node material with is_readonly=False. This creates a new nodetree
            which can then be populated with imported values
                - wrapping an already-populated nodetree may have strange results.
        - wrap an existing node material with is_readonly=True. This searches for nodes
            and exposes the nodes it finds, whose values can then be exported.
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

    def __init__(self, material, is_readonly=True, use_nodes=True, use_alpha=False):
        self.is_readonly = is_readonly
        self.material = material
        self.use_alpha = use_alpha
        if not is_readonly:
            self.use_nodes = use_nodes
        self.update()

    def update(self):
        for node in self.NODES_LIST:
            setattr(self, node, None)
        self._grid_locations = set()

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
        self._node_image_texture = node_image_texture
        self._node_texcoords = ...  # lazy initialization

    def use_nodes_get(self):
        return self.material.use_nodes

    @_set_check
    def use_nodes_set(self, val):
        self.material.use_nodes = val
        self.update()

    use_nodes = property(use_nodes_get, use_nodes_set)

    def node_image_texture_get(self):
        if not self.use_nodes:
            return None
        elif self._node_image_texture is not None:
            return self._node_image_texture
        elif self.is_readonly:
            return None

        # create new Image Texture...
        tree = self.material.node_tree
        nodes = tree.nodes
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
        if self.use_alpha:
            tree.links.new(
                node_image_texture.outputs["Alpha"],
                self.node_principled_bsdf.inputs["Alpha"],
            )

        self._node_image_texture = node_image_texture
        return self._node_texcoords

    node_image_texture = property(node_image_texture_get)

    def image_get(self):
        return (
            self.node_image_texture.image
            if self.node_image_texture is not None
            else None
        )

    @_set_check
    def image_set(self, image):
        self.node_image_texture.image = image

    image = property(image_get, image_set)

    def projection_get(self):
        return (
            self.node_image_texture.projection
            if self.node_image_texture is not None
            else "FLAT"
        )

    @_set_check
    def projection_set(self, projection):
        self.node_image_texture.projection = projection

    projection = property(projection_get, projection_set)

    def texcoords_get(self):
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
            # Create new Texture Coordinates node...
            tree = self.material.node_tree
            node_texcoords = tree.nodes.new(type="ShaderNodeTexCoord")
            node_texcoords.label = "Texture Coordinates"
            self._grid_to_location(-1, 1, dst_node=node_texcoords)
            # ... and link it to the image texture node
            socket_dst = self.node_image_texture.inputs["Vector"]
            socket_src = node_texcoords.outputs["UV"]
            tree.links.new(socket_src, socket_dst)
            self._node_texcoords = node_texcoords

        if self.node_image_texture is not None:
            socket = self.node_image_texture.inputs["Vector"]
            if socket.is_linked:
                return socket.links[0].from_socket.name
        return "UV"

    @_set_check
    def texcoords_set(self, texcoords):
        # Image texture node already defaults to UVs, no extra node needed.
        if texcoords == "UV":
            return
        self.texcoords_get()  # make sure texcoord node exists first
        tree = self.material.node_tree
        node_dst = self.node_image_texture
        socket_src = self._node_texcoords.outputs[texcoords]
        tree.links.new(socket_src, node_dst.inputs["Vector"])

    texcoords = property(texcoords_get, texcoords_set)

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
