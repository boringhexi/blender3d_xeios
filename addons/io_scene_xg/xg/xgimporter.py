"""xgimporter.py: import XgScene into Blender"""
import os.path
import re
from collections import defaultdict
from itertools import chain
from math import radians
from operator import neg
from typing import (
    AnyStr,
    Collection,
    DefaultDict,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import bpy
import mathutils
from bpy.types import Action
from bpy_extras.io_utils import unpack_list
from mathutils import Matrix, Quaternion, Vector

from ..materials.wrapper import (
    MyPrincipledBSDFWrapper,
    xgmaterial_uses_alpha,
    xgspecular_to_roughness,
)
from .xganimsep import AnimSepEntry, read_animseps
from .xgerrors import XgImportError
from .xgscene import (
    Constants,
    DagChildren,
    XgBgMatrix,
    XgBone,
    XgDagMesh,
    XgDagNode,
    XgDagTransform,
    XgMaterial,
    XgNode,
    XgScene,
)
from .xgscenereader import XgSceneReader

xg_to_blender_interp_type = {
    Constants.InterpolationType.NONE: "CONSTANT",
    Constants.InterpolationType.LINEAR: "LINEAR",
}


def _tridata_to_prims(tridata: Collection[int], primtype: int) -> List[Tuple[int, ...]]:
    """return a list of prims from tridata, each prim is a tuple of vertex indices

    (helper function used by _tri_indices_from_dagmesh)
    Returns a list of prims from tridata, where each prim is a tuple of vertex
    indices representing a single prim (such as a triangle strip). It is up to the
    caller to know what kind of prim it is and what to do with it.
    For example, turn triFanData into a list of triFan prims.

    :param tridata: a list of triListData, triStripData, or triFanData from an
        xgDagMesh. (not sure how it would handle primData, effectively unsupported)
    :param primtype: value from xgDagMesh.primType (see xgscene.Constants.PrimType)
        that tells us how prims are stored in tridata.
        KICKSEP (i.e. 4):  each prim is an int followed by that many vertex indices
        KICKGROUP (i.e. 5): the first int is the starting vertex index; all ints after
        that are the number of consecutive vertex indices to use in the next prim
        any other value:  raises ValueError
    :return: a list of prims, where each prim is a tuple of vertex indices
    """
    if not tridata:
        return []
    prims = []
    if primtype == Constants.PrimType.KICKSEP:
        # split tridata into separate prims
        tridata_offset = 0
        while tridata_offset < len(tridata):
            prim_size = tridata[tridata_offset]
            tridata_offset += 1
            prims.append(tridata[tridata_offset : tridata_offset + prim_size])
            tridata_offset += prim_size
    elif primtype == Constants.PrimType.KICKGROUP:
        # recreate the prims
        vertex_index = tridata[0]  # starting vertex index
        for num_verts in tridata[1:]:
            prims.append(tuple(range(vertex_index, vertex_index + num_verts)))
            vertex_index += num_verts
    else:
        raise ValueError(f"unexpected primtype ({primtype})")
    return prims


def _url_to_png(url: str, dir_: str) -> Optional[str]:
    """return path to a png file in dir_ that matches url

    will check for url.png first, then url.(rgba32|rgb24|i8|i4).png. If no match is
    found, return None

    :param url: IMX filename from xgTexture.url
    :param dir_: directory to look in
    :return: path to existing PNG that matches url
    """
    urlbase = re.escape(url[:-4])  # remove ".imx" from end
    dot = re.escape(os.path.extsep)
    filesonly = [f for f in os.listdir(dir_) if os.path.isfile(os.path.join(dir_, f))]

    # try 1: url.png
    pattern_try1 = f"^{urlbase}{dot}png$"
    matches = [f for f in filesonly if re.match(pattern_try1, f, re.IGNORECASE)]
    if matches:
        return os.path.join(dir_, matches[0])

    # try 2: url.(rgba32|rgb24|i8|i4).png
    pattern_try2 = f"^{urlbase}{dot}(rgba32|rgb24|i8|i4){dot}png$"
    matches = [f for f in filesonly if re.match(pattern_try2, f, re.IGNORECASE)]
    if matches:
        return os.path.join(dir_, matches[0])

    return None


def _make_simplified_dag(
    dagparent: XgDagNode,
    dagchildren: DagChildren,
    simplified_dag: DefaultDict[XgDagNode, List[XgDagMesh]],
) -> None:
    """create a simplified DAG that isn't nested and contains the bare minimum

    Create a simplified, non-nested version of the DAG. From the original DAG, it
    will only contain:

    - top-most xgDagMeshes
    - only those xgDagTransforms that directly parent one or more xgDagMeshes
    - the child xgDagMeshes of those xgDagTransforms

    In addition, xgDagTransforms lacking an inputMatrix (i.e. having no pose/animation
    data) will also be omitted.

    (This is specifically meant to target Stage 8's BAND_NOR.XG model, the only one
        in the game that has a nested DAG, and which can be faithfully represented by
        a non-nested DAG)

    :param dagparent: dag parent from a DAG
    :param dagchildren: dag children from a DAG
    :param simplified_dag: starts as an empty defaultdict(list), becomes populated
        with the simplified DAG
    :return: None, but simplified_dag will become populated
    """
    if dagparent.xgnode_type == "xgDagMesh":
        # assuming an xgDagMesh parent never has children
        simplified_dag[dagparent] = []
        return
    elif dagparent.xgnode_type == "xgDagTransform":
        if not dagchildren:
            return
        for dagchild in dagchildren:
            if dagchild.xgnode_type == "xgDagMesh":
                # assuming an xgDagMesh child never has children
                if hasattr(dagparent, "inputMatrix"):
                    simplified_dag[dagparent].append(dagchild)
                else:
                    # omit the un-animated xgDagTransform parent, as it has no effect
                    simplified_dag[dagchild] = []
            else:  # dagchild.xgnode_type == "xgDagTransform"
                _make_simplified_dag(dagchild, dagchildren[dagchild], simplified_dag)


def _correct_pose_matrix_axes(pose_matrix: Matrix) -> Matrix:
    """calculate and return the axis-corrected pose matrix

    Take a Blender posebone matrix originating from an XG model and using the XG axis
        system, return the equivalent matrix in Blender's axis system

    :param pose_matrix: Matrix intended for a Blender posebone
    :return: equivalent Matrix with axes corrected to Blender's axis system
    """
    correction_scalex = Matrix.Scale(-1, 4, Vector((1, 0, 0)))
    # correction_scalex is used twice (applying and removing scale) to
    # "mirror" bone rotations across the Y axis.
    # https://math.stackexchange.com/questions/3840143

    correction_rotxz = Matrix.Rotation(radians(180), 4, "Z") @ Matrix.Rotation(
        radians(90), 4, "X"
    )

    pose_matrix = correction_rotxz @ correction_scalex @ pose_matrix @ correction_scalex
    return pose_matrix


def _flatten_prs_is_animated(
    prs_is_animated1: Tuple[bool, bool, bool], prs_is_animated2: Tuple[bool, bool, bool]
) -> Tuple[bool, bool, bool]:
    """ORs the respective bools from each tuple

    :param prs_is_animated1: 1 bool each for position,rotation,scale being animated
    :param prs_is_animated2: same
    :return:
    """
    return tuple(a or b for a, b in zip(prs_is_animated1, prs_is_animated2))


class XgImporter:
    """imports an XgScene into Blender"""

    def __init__(
        self,
        xgscene: XgScene,
        texturedir: Optional[AnyStr] = None,
        xganimseps: Sequence[AnimSepEntry] = None,
        bl_name: str = "UNNAMED",
        global_import_scale: float = 1.0,
    ):
        """create an XgImporter to import the XgScene into Blender

        :param xgscene: xgscene.XgScene instance, the XgScene to be imported
        :param texturedir: directory in which to search for textures. if None, textures
            will not be imported (will create placeholders)
        :param xganimseps: sequence of AnimSep entries. if None, animations will not be
            imported
        :param bl_name: the imported model will be given this name in Blender
        :param global_import_scale: value by which to scale imported meshes and armature
        """
        self._xgscene = xgscene
        self._texturedir = texturedir
        self._xganimseps = xganimseps
        self._bl_name = bl_name
        self._global_import_scale = global_import_scale
        self.warnings = []

        class ImporterOptions:
            def __init__(self):
                self.import_textures = texturedir is not None
                self.import_animations = xganimseps is not None

        class ImporterDebugOptions:
            def __init__(self):
                self.correct_mesh_axes = True
                self.correct_restpose_axes = True
                self.correct_pose_axes = True

        self.options = ImporterOptions()
        self.debugoptions = ImporterDebugOptions()

        if texturedir is None:
            self.warn("No texture directory provided, textures will not be imported")
        if xganimseps is None:
            self.warn("No animseps data provided, animations will not be imported")

        self._bpycollection = None
        self._bpyarmatureobj = None

        class Mappings:
            """holds relationships between XgScene data and Blender data"""

            def __init__(self):
                self.xgdagmesh_bpymeshobj: Dict[XgDagMesh, bpy.types.Object] = dict()
                self.xgdagtransform_bpybonename: Dict[XgDagTransform, str] = dict()
                self.xgbone_bpybonename: Dict[XgBone, str] = dict()
                self.regmatnode_bpymat: Dict[XgMaterial, bpy.types.Material] = dict()
                self.bpybonename_restscale: Dict[str, Vector] = dict()
                self.bpybonename_previousquat = dict()

        self._mappings = Mappings()

    @classmethod
    def from_path(cls, xgscenepath: str, **kwargs) -> "XgImporter":
        """from xgscenepath, create an XgImporter to import the XgScene into Blender

        paths of the xganimseps and textures will be derived from xgscenepath

        :param xgscenepath: path to an XG file
        :return: an XgImporter instance
        """
        bl_name = os.path.basename(xgscenepath)
        texturedir = os.path.dirname(xgscenepath)
        xgscenereader = XgSceneReader.from_path(xgscenepath, autoclose=True)
        xgscene = xgscenereader.read_xgscene()

        animseppath = f"{os.path.splitext(xgscenepath)[0]}{os.path.extsep}animsep"
        try:
            animseps = read_animseps(animseppath)
        except FileNotFoundError:
            animseps = None
        return cls(xgscene, texturedir, animseps, bl_name=bl_name, **kwargs)

    def _get_armature(self, mode: Optional[str] = None) -> bpy.types.Object:
        """return Blender armature object, will be created if it doesn't exist yet

        :param mode: one of "OBJECT", "EDIT", or "POSE", the mode to set Blender to,
            or None to leave it in the same mode as before.
        :return: Blender armature object
        """
        if self._bpyarmatureobj is None:
            # create Blender armature
            arm_name = f"{self._bl_name}_arm"
            bpyarmdata = bpy.data.armatures.new(arm_name)
            bpyarmobj = bpy.data.objects.new(bpyarmdata.name, bpyarmdata)
            self._bpycollection.objects.link(bpyarmobj)
            self._bpyarmatureobj = bpyarmobj
        else:
            # retrieve existing Blender armature
            bpyarmobj = self._bpyarmatureobj

        # set the Blender mode with the armature as the active object
        if mode:
            bpy.context.view_layer.objects.active = bpyarmobj
            bpy.ops.object.mode_set(mode=mode)

        return bpyarmobj

    def import_xgscene(self) -> None:
        """import the XgScene into Blender

        1) Initialize objects & set up hierarchy
        2) Load textures
        3) Load regular materials
        4) Load multipass materials
        5) Load meshes
        6) Load armature bones
        7) Load animations

        After all calls to this method are done (i.e. caller imported all models it
        wants to import), the caller should do bpy.context.view_layer.update() to update
        Blender's viewport display
        """
        # back to object mode (in case we need to do armature stuff)
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        # 0) Create new Collection to import into
        collection = bpy.data.collections.new(self._bl_name)
        bpy.context.scene.collection.children.link(collection)
        self._bpycollection = collection

        # 1) Initialize objects & set up hierarchy
        self._init_objs_hierarchy_from_dag()

        # 3) Load materials (and textures)
        if self._mappings.regmatnode_bpymat:
            self._load_materials()

        # 5) Load meshes
        if self._mappings.xgdagmesh_bpymeshobj:
            self._load_meshes()

        # 6) Load armature bones
        if self._mappings.xgbone_bpybonename:
            self._load_bones()

        # 6.5 load initial pose
        self._load_initial_pose()

        # 7) Load animations
        if self.options.import_animations:
            original_frame = bpy.context.scene.frame_current
            self._load_anims()
            bpy.context.scene.frame_set(original_frame)

        # back to object mode
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

    def _init_objs_hierarchy_from_dag(self):
        """create empty Blender objects and link them in the right hierarchy

        Create empty Blender objects and link them (e.g. assign textures to materials).
        XgScene data will not be loaded into the Blender objects yet.
        """
        dag = self._xgscene.dag
        simplified_dag: DefaultDict[XgNode, list[XgNode]] = defaultdict(list)
        for dagparent, dagchildren in dag.items():
            _make_simplified_dag(dagparent, dagchildren, simplified_dag)

        for dagnode, dagchildren in simplified_dag.items():
            # For xgDagTransforms, create a bone to act as the transform, then
            # create child meshes and parent them to the bone.
            if dagnode.xgnode_type == "xgDagTransform":
                # init bone to use for the xgDagTransform
                bpybone_name = self._init_bone_from_bonenode(dagnode)
                # init meshes to be parented to the xgDagTransform's bone
                bpymeshobjs = [
                    self._init_mesh_from_dagmeshnode(xgdagmesh, True)
                    for xgdagmesh in dagchildren
                ]

                if bpybone_name is not None:  # if a bone was made,
                    # then parent meshes to the xgDagTransform
                    for bpymeshobj in bpymeshobjs:
                        # skip meshes that were not created
                        if bpymeshobj is not None:
                            bpymeshobj.parent = self._get_armature(mode="POSE")
                            bpymeshobj.parent_type = "BONE"
                            bpymeshobj.parent_bone = bpybone_name
                            bpymeshobj.matrix_world = Matrix()

            # For xgDagMeshes, just create the mesh
            elif dagnode.xgnode_type == "xgDagMesh":
                self._init_mesh_from_dagmeshnode(dagnode)

            # For other non-DAG node types, warn and skip
            else:
                self.warn(f"Unexpected node type {dagnode} in dag, skipping")

    def _init_bone_from_bonenode(
        self, bonenode: Union[XgDagTransform, XgBone]
    ) -> Optional[str]:
        """init a new Blender bone from bonenode, return Blender bone name

        :param bonenode: XgDagTransform or XgBone
        :return: Blender bone's name, or None if the bone was not created
            (because it has no inputMatrix which means it would have no effect)
        """
        if bonenode.xgnode_type == "xgDagTransform":
            bonename_mapping = self._mappings.xgdagtransform_bpybonename
        elif bonenode.xgnode_type == "xgBone":
            bonename_mapping = self._mappings.xgbone_bpybonename
        else:
            raise ValueError(f"{bonenode} isn't an XgDagTransform or XgBone")

        bpyarmobj = self._get_armature(mode="EDIT")
        # TODO temporary armature view stuff for my convenience
        bpyarmobj.data.show_axes = True
        bpyarmobj.show_in_front = True

        if bonenode not in bonename_mapping:
            # initialize new Blender bone
            if hasattr(bonenode, "inputMatrix"):
                # create new Blender bone in armature
                bpyeditbone = bpyarmobj.data.edit_bones.new(name=bonenode.xgnode_name)
                bpybone_name = bpyeditbone.name
                bonename_mapping[bonenode] = bpybone_name

                # tail of (0,1,0) required to for xgDagTransform bones
                bpyeditbone.tail = (0, 1, 0)

                if self.debugoptions.correct_restpose_axes:
                    correction_scalex = Matrix.Scale(-1, 4, Vector((1, 0, 0)))
                    correction_rotxz = Matrix.Rotation(
                        radians(180), 4, "Z"
                    ) @ Matrix.Rotation(radians(90), 4, "X")
                    bpyeditbone.matrix = (
                        correction_rotxz
                        @ correction_scalex
                        @ bpyeditbone.matrix
                        @ correction_scalex
                    )
            else:
                # bone has no inputMatrix, so don't bother
                return None

            bonename_mapping[bonenode] = bpybone_name
        else:
            # retrieve existing bone
            bpybone_name = bonename_mapping[bonenode]
        return bpybone_name

    def _init_mesh_from_dagmeshnode(
        self, dagmeshnode: XgDagMesh, dagtransform: bool = False
    ) -> Optional[bpy.types.Object]:
        """create empty Blender mesh object or retrieve existing one, return it

        If xgdagmesh has not been encountered before, create an empty Blender mesh
        object for it and add it to the scene. Otherwise, grab the existing one.
        And if xgdagmesh isn't really an XgDagMesh, return None and don't create
        anything in Blender.

        :param dagmeshnode: XgDagMesh
        :param dagtransform: True if this xgDagMesh is transformed by an xgDagTransform.
            Only used to issue a warning if it's also being transformed by an xgEnvelope
        :return: Blender Object containing Mesh data, or None if xgdagmesh wasn't
            actually an XgDagMesh
        """
        if dagmeshnode.xgnode_type != "xgDagMesh":
            self.warn(
                f"{dagmeshnode} is not an xgDagMesh, cannot add as a mesh. Skipping"
            )
            return None

        mesh_mapping = self._mappings.xgdagmesh_bpymeshobj

        # if mesh doesn't exist yet, create it
        if dagmeshnode not in mesh_mapping:
            bpymeshdata = bpy.data.meshes.new(dagmeshnode.xgnode_name)
            bpymeshobj = bpy.data.objects.new(bpymeshdata.name, bpymeshdata)
            self._bpycollection.objects.link(bpymeshobj)
            mesh_mapping[dagmeshnode] = bpymeshobj

            # create material if it doesn't exist yet
            if hasattr(dagmeshnode, "inputMaterial"):
                matnode = dagmeshnode.inputMaterial[0]

                # initialize from a xgMaterial, or take the first xgMaterial in a
                # xgMultiPassMaterial and initialize from that
                if matnode.xgnode_type in ("xgMaterial", "xgMultiPassMaterial"):
                    if matnode.xgnode_type == "xgMultiPassMaterial":
                        self.warn(
                            f"We can't do multi-layer materials yet ({matnode}), "
                            "so for now let's fake it by loading the first "
                            "material as a regular material"
                        )
                        matnode = matnode.inputMaterial[0]

                    # create new material or retrieve an existing one
                    if matnode not in self._mappings.regmatnode_bpymat:
                        bpymat = bpy.data.materials.new(name=matnode.xgnode_name)
                        self._mappings.regmatnode_bpymat[matnode] = bpymat

                    else:
                        bpymat = self._mappings.regmatnode_bpymat[matnode]
                    # either way, add the material to this mesh
                    bpymeshdata.materials.append(bpymat)
                    bpymeshobj.active_material = bpymat

                else:
                    raise XgImportError(
                        f"{dagmeshnode} has unexpected material node type {matnode}"
                    )

            # initialize mesh's bones
            envelopenodes = []
            bggeometrynode = dagmeshnode.inputGeometry[0]
            if hasattr(bggeometrynode, "inputGeometry"):
                envelopenodes = [
                    n
                    for n in bggeometrynode.inputGeometry
                    if n.xgnode_type == "xgEnvelope"
                ]
            if envelopenodes:
                if dagtransform:
                    self.warn(
                        f"{dagmeshnode} is transformed by both xgDagTransform "
                        "and xgEnvelope; may have strange results"
                    )
                for envnode in envelopenodes:
                    # inputMatrix1[0] is xgBone
                    self._init_bone_from_bonenode(envnode.inputMatrix1[0])

                # make armature the parent of this mesh
                bpyarmobj = self._get_armature(mode="EDIT")
                bpyarmmod = bpymeshobj.modifiers.new(bpyarmobj.name, "ARMATURE")
                bpyarmmod.object = bpyarmobj
                bpymeshobj.parent = bpyarmobj

        # if mesh already exists, retrieve it
        else:
            # TODO mesh instancing, I don't think any actual gman models do this?
            #  I think maybe all I have to do is retrieve the meshdata block and create
            #  and return a new bpyobj linked to that same meshdata?
            self.warn(
                f"{dagmeshnode} is used multiple times, but it will only appear once "
                "until mesh instancing/linked duplicates/whatever is implemented"
            )
            bpymeshobj = mesh_mapping[dagmeshnode]

        return bpymeshobj

    def _load_materials(self) -> None:
        """load material data from XG scene into the initialized Blender materials"""
        for matnode, bpymat in list(self._mappings.regmatnode_bpymat.items()):
            matwrap = MyPrincipledBSDFWrapper(
                bpymat, is_readonly=False, use_alpha=xgmaterial_uses_alpha(matnode)
            )

            # set color + alpha
            matwrap.base_color = matnode.diffuse[0:3]
            matwrap.alpha = matnode.diffuse[3]

            # set specular + roughness
            matwrap.roughness = xgspecular_to_roughness(matnode.specular[3])
            rgb = matnode.specular[0:3]
            matwrap.specular = (rgb[0] + rgb[1] + rgb[2]) / 3  # average color

            # set texture
            if hasattr(matnode, "inputTexture"):
                # Look for a likely PNG in the same dir based on texnode.url
                texnode = matnode.inputTexture[0]
                imagepath = _url_to_png(texnode.url, self._texturedir)

                if imagepath is not None and self.options.import_textures:
                    # load it, and set it as the texture's image
                    bpyimage = bpy.data.images.load(imagepath, check_existing=True)

                else:
                    # create a placeholder if we tried to import a texture
                    # and failed, or if we're not importing textures at all.
                    # but only warn if we tried and failed
                    if self.options.import_textures and imagepath is None:
                        self.warn(
                            "no suitable PNG file was found for texture "
                            f"{texnode.url!r}, creating placeholder instead"
                        )
                    # TODO reuse existing placeholder for repeated images
                    #  (images with the same url within this model)
                    bpyimage = bpy.data.images.new(texnode.url, 128, 128)
                    bpyimage.filepath = os.path.join(self._texturedir, texnode.url)
                    bpyimage.source = "FILE"
                bpyimage.name = texnode.url
                matwrap.image = bpyimage

            # set texcoords
            if matnode.textureEnv == Constants.TextureEnv.SPHEREMAP:
                matwrap.texprojection = "SPHERE"
                matwrap.texcoords = "Reflection"

            # set use_backface_culling
            matwrap.use_backface_culling = False
            # TODO makes everything double-sided for now. support properly one day
            # (depends on xgDagMesh, not xgMaterial)

            if matwrap.use_alpha:
                matwrap.use_eevee_alpha_blend = True

    def _load_meshes(self) -> None:
        """load mesh data from the XG scene into the initialized Blender meshes"""
        for dagmeshnode, bpymeshobj in list(
            self._mappings.xgdagmesh_bpymeshobj.items()
        ):
            if dagmeshnode.primType not in (
                Constants.PrimType.KICKSEP,
                Constants.PrimType.KICKGROUP,
            ):
                self.warn(
                    f"{dagmeshnode} has unsupported primType ({dagmeshnode.primType}), "
                    "its mesh data will not be loaded (send the author a sample!)"
                )
                continue

            bpymeshdata = bpymeshobj.data
            dagmeshverts = dagmeshnode.inputGeometry[0].vertices

            # # Populate Blender mesh with vertices and faces # #
            # scaling and axis correction from XG to Blender:
            gis = self._global_import_scale
            if self.debugoptions.correct_mesh_axes:
                bpyvertcoords = [
                    (x * gis, z * gis, y * gis) for x, y, z in dagmeshverts.coords
                ]
            else:
                bpyvertcoords = [
                    (x * gis, y * gis, z * gis) for x, y, z in dagmeshverts.coords
                ]
            bpytriindices = self._tri_indices_from_dagmesh(dagmeshnode)
            bpymeshdata.from_pydata(bpyvertcoords, [], bpytriindices)

            # # Load normals # #
            if dagmeshverts.normals:
                if self.debugoptions.correct_mesh_axes:
                    bpynormals = [(x, z, y) for x, y, z in dagmeshverts.normals]
                else:
                    bpynormals = dagmeshverts.normals
                bpymeshdata.normals_split_custom_set_from_vertices(bpynormals)
                bpymeshdata.use_auto_smooth = True

            # # Load vertex colors # #
            # TODO "Deprecated, use color_attributes instead"
            if dagmeshverts.colors:
                bpyvcolorlayer = bpymeshdata.vertex_colors.new()
                loop_vcolors = (
                    dagmeshverts.colors[lo.vertex_index] for lo in bpymeshdata.loops
                )
                bpyvcolorlayer.data.foreach_set("color", unpack_list(loop_vcolors))

            # # Load texture coordinates # #
            if dagmeshverts.texcoords:
                bpyuvlayer = bpymeshdata.uv_layers.new()
                loop_uvs_flat = unpack_list(
                    dagmeshverts.texcoords[lo.vertex_index] for lo in bpymeshdata.loops
                )
                # texcoords are upside-down, so reverse the vertical axes
                loop_uvs_flat[1::2] = map(neg, loop_uvs_flat[1::2])
                bpyuvlayer.data.foreach_set("uv", loop_uvs_flat)
                del loop_uvs_flat

            # # Load vertex groups
            geomnode = dagmeshnode.inputGeometry[0]
            envelopenodes = []
            if hasattr(geomnode, "inputGeometry"):
                envelopenodes = [
                    node
                    for node in geomnode.inputGeometry
                    if node.xgnode_type == "xgEnvelope"
                ]
            for envnode in envelopenodes:
                bonenode = envnode.inputMatrix1[0]
                bpybonename = self._mappings.xgbone_bpybonename[bonenode]
                bpymeshobj.vertex_groups.new(name=bpybonename)
                bpymeshobj.vertex_groups[bpybonename].add(
                    unpack_list(envnode.vertexTargets), 1, "ADD"
                )

            # # TODO finalize
            # validate mesh (in case there's weird stuff)
            # make double-sided if dagmesh is so

    def _tri_indices_from_dagmesh(
        self, dagmeshnode: XgDagMesh, fix_winding_order: bool = True
    ) -> List[Tuple[int, int, int]]:
        """return a list of triangles (vert indices) from dagmeshnode

        (helper method used by _load_meshes)

        :param dagmeshnode: XgDagMesh containing the triangles
        :param fix_winding_order: if True, reverse triangle winding order where
            necessary to prevent Blender's auto-generated normals from looking weird.
            Without this fix, badly-lit surfaces may appear.
        :return: List of 3-tuples of vertex indices, each 3-tuple defines a triangle
        """
        # TODO not now: account for dagmesh using different winding orders
        #  i.e. in Blender CW is forward-facing, so if CullFunc.CCWFRONT then reverse
        #  all winding order
        #  (though all gman models use double-sided)
        #  Blender materials have a Backface Culling property, enable it when dagmesh is
        #  not double-sided

        if hasattr(dagmeshnode, "primData") and dagmeshnode.primData:
            self.warn(
                f"{dagmeshnode}'s primData will not be imported "
                "(primData is still unknown, send the author a sample!)"
            )

        triangles = []

        # Triangle lists:
        trilists = _tridata_to_prims(dagmeshnode.triListData, dagmeshnode.primType)
        for trilist in trilists:
            tris = (trilist[i : i + 3] for i in range(0, len(trilist) - 2, 3))
            # trilist winding order needs to be reversed (unless the mesh has been axis-
            # corrected, in which case the current winding order is already correct)
            if not self.debugoptions.correct_mesh_axes and fix_winding_order:
                tris = [[tri[2], tri[1], tri[0]] for tri in tris]
            triangles.extend(tris)

        # Triangle strips:
        # triangle strips in this game seem to have semi-random winding order, leading
        # to the problem described in the dosctring and resolved by fix_winding_order
        dagcoords = dagmeshnode.inputGeometry[0].vertices.coords
        dagnormals = dagmeshnode.inputGeometry[0].vertices.normals
        fix_tristrip_winding_order = (
            fix_winding_order
            and dagnormals
            and (dagmeshnode.cullFunc == Constants.CullFunc.TWOSIDED)
        )
        tristrips = _tridata_to_prims(dagmeshnode.triStripData, dagmeshnode.primType)
        for tristrip in tristrips:
            tristrip_tris = []
            for i in range(len(tristrip) - 2):
                tri = tuple(tristrip[i : i + 3])
                # reverse winding of odd-numbered triangles
                if not self.debugoptions.correct_mesh_axes and i % 2 == 1:
                    tri = (tri[1], tri[0], tri[2])
                # (or do the opposite if this is an axis-corrected mesh)
                elif self.debugoptions.correct_mesh_axes and i % 2 == 0:
                    tri = (tri[1], tri[0], tri[2])
                tristrip_tris.append(tri)

            # Make sure this triangle strip has the correct winding order. That is,
            # if this triangle strip's normals generally agree with the normals
            # Blender would calculate for it, it's already good; otherwise, reverse
            # this triangle strip's winding order so that Blender's calculated
            # normals (which depend on winding order) will agree.
            if fix_tristrip_winding_order:
                normals_alldiffs = []
                for tri in tristrip_tris:
                    # get average vertex normal of this triangle
                    tri_dagnormals = (Vector(dagnormals[vertidx]) for vertidx in tri)
                    tri_average_dagnormal = sum(tri_dagnormals, Vector()) / 3.0
                    # get Blender's calculated face normal of this triangle
                    tri_dagcoords = tuple(Vector(dagcoords[vertidx]) for vertidx in tri)
                    bl_facenormal = mathutils.geometry.normal(tri_dagcoords)
                    # calculate the difference between the two
                    normals_diff = tri_average_dagnormal.angle(bl_facenormal, None)
                    if normals_diff is None:
                        # unable to calculate a difference between normals
                        # e.g. degenerate triangle with 0 area
                        continue
                    normals_alldiffs.append(normals_diff)

                # Go through all the normal differences and check:
                if normals_alldiffs:
                    avg_normal_diff = sum(normals_alldiffs) / len(normals_alldiffs)
                    normals_disagree = avg_normal_diff > radians(90)

                    # If the tristrip's normals generally disagree with Blender's
                    # calculated normals...
                    if not self.debugoptions.correct_mesh_axes and normals_disagree:
                        # ...reverse the winding order.
                        tristrip_tris = ((t[1], t[0], t[2]) for t in tristrip_tris)

                    # (or do the opposite if this is an axis-corrected mesh)
                    elif self.debugoptions.correct_mesh_axes and not normals_disagree:
                        tristrip_tris = ((t[1], t[0], t[2]) for t in tristrip_tris)

            triangles.extend(tristrip_tris)

        # Triangle fans:
        # TODO untested, as no known models use trifans
        trifans = _tridata_to_prims(dagmeshnode.triFanData, dagmeshnode.primType)
        for trifan in trifans:
            tris = (
                (trifan[0], trifan[i + 1], trifan[i + 2])
                for i in range(len(trifan) - 2)
            )
            # trifan winding order needs to be reversed (unless the mesh has been axis-
            # corrected, in which case the current winding order is already correct)
            if not self.debugoptions.correct_mesh_axes and fix_winding_order:
                tris = [(tri[2], tri[1], tri[0]) for tri in tris]
            triangles.extend(tris)

        return triangles

    def _load_bones(self):
        """load bone data from the XG scene into the initialized Blender bones"""
        BONE_SIZE = 0.25  # TODO there is a better way, eventually
        bpyarmobj = self._get_armature(mode="EDIT")

        for bonenode, bpybonename in self._mappings.xgbone_bpybonename.items():
            # get the original rest pose components (position, rotation, and scale)
            rmtx = bonenode.restMatrix
            rmatrixti = Matrix((rmtx[:4], rmtx[4:8], rmtx[8:12], rmtx[12:]))
            rmatrixti.transpose()
            rmatrixti.invert()
            restpos, restrot, restscl = rmatrixti.decompose()

            # get the Blender edit bone we'll be setting the rest pose for
            bpyeditbone = bpyarmobj.data.edit_bones[bpybonename]

            # combine position/rotation into a Blender rest pose
            restpos_matrix = Matrix.Translation(restpos * self._global_import_scale)
            restrot_matrix = restrot.to_matrix().to_4x4()
            uncorrected_bpyeditbone_matrix = restpos_matrix @ restrot_matrix

            if self.debugoptions.correct_restpose_axes:
                # calculate and apply the axis-corrected rest pose
                correction_scalex = Matrix.Scale(-1, 4, Vector((1, 0, 0)))
                # correction_scalex is used twice (applying and removing scale) to
                # "mirror" bone rotations across the Y axis.
                # https://math.stackexchange.com/questions/3840143
                correction_rotxz = Matrix.Rotation(
                    radians(180), 4, "Z"
                ) @ Matrix.Rotation(radians(90), 4, "X")
                bpyeditbone.matrix = (
                    correction_rotxz
                    @ correction_scalex
                    @ uncorrected_bpyeditbone_matrix
                    @ correction_scalex
                )
            else:
                # apply the uncorrected rest pose
                bpyeditbone.matrix = uncorrected_bpyeditbone_matrix

            # rest scale: save for later
            # XG's rest poses can have rest scale, but Blender's can't. So later, we'll
            # use rest scale to adjust the pose scale, thereby achieving the same effect
            # (axis correction will happen then, not now)
            self._mappings.bpybonename_restscale[bpybonename] = restscl

            bpyeditbone.length = BONE_SIZE

    def _load_initial_pose(self):
        """pose the bones

        editbones must already be in rest position.
        this is not the same an animation pose or rest pose; this "default" pose is
        likely to get overwritten by an animation, but sometimes is not (e.g. Noren's
        feet are un-animated and need to be posed this way to match the animation)
        """
        bpybonename_restscale = self._mappings.bpybonename_restscale
        bpyarmobj = self._get_armature(mode="POSE")
        both_bone_types = chain(
            self._mappings.xgdagtransform_bpybonename.items(),
            self._mappings.xgbone_bpybonename.items(),
        )

        for bonenode, bpybonename in both_bone_types:
            if not hasattr(bonenode, "inputMatrix") or not bonenode.inputMatrix:
                continue

            # get the Blender posebone to be posed + the xgBgMatrix containing the pose
            bpyposebone = bpyarmobj.pose.bones[bpybonename]
            bpyposebone.rotation_mode = "QUATERNION"
            bgmatrixnode = bonenode.inputMatrix[0]

            pose_matrix = self._calc_flattened_initialpose_matrix(
                bgmatrixnode, restscale=bpybonename_restscale.get(bpybonename)
            )
            if self.debugoptions.correct_pose_axes:
                pose_matrix = _correct_pose_matrix_axes(pose_matrix)
            bpyposebone.matrix = pose_matrix

    def _calc_flattened_initialpose_matrix(
        self,
        bgmatrixnode: XgBgMatrix,
        restscale: Optional[Tuple[float, float, float]] = None,
    ) -> Matrix:
        """return the matrix of this bgmatrixnode multiplied by all its parents

        :param bgmatrixnode: XgBgMatrix node
        :param restscale: (x, y, z) or None. The rest scale this bone would have if
            Blender supported bone rest scale. Used to correct the pose scale.
        :return: Blender Matrix
        """
        this_pose_matrix = self._calc_pose_matrix(
            bgmatrixnode.position,
            bgmatrixnode.rotation,
            bgmatrixnode.scale,
            restscale=restscale,
        )
        if (
            hasattr(bgmatrixnode, "inputParentMatrix")
            and bgmatrixnode.inputParentMatrix
        ):
            parent_pose_matrix = self._calc_flattened_initialpose_matrix(
                bgmatrixnode.inputParentMatrix[0]
            )
            return parent_pose_matrix @ this_pose_matrix
        else:
            return this_pose_matrix

    def _calc_pose_matrix(
        self,
        position: Optional[Tuple[float, float, float]],
        rotation: Optional[Tuple[float, float, float, float]],
        scale: Optional[Tuple[float, float, float]],
        restscale: Optional[Tuple[float, float, float]] = None,
        bone_name_quat_compat=None,
    ) -> Matrix:
        """calculate and return the matrix to position a Blender pose bone

        :param position: (x, y, z) or None
        :param rotation: quaternion (x, y, z, w) or None
        :param scale: (x, y, z) or None
        :param restscale: (x, y, z) or None. The rest scale this bone would have if
            Blender supported bone rest scale. Used to correct the pose scale.
        :return:
        """
        # calculate pose position
        if position is not None:
            posmtx = Matrix.Translation(
                (c * self._global_import_scale for c in position)
            )
        else:
            posmtx = Matrix.Identity(4)

        # calculate pose rotation
        if rotation is not None:
            rotx, roty, rotz, rotw = rotation
            rotquat = Quaternion((rotw, rotx, roty, rotz))
            rotquat_axis, rotquat_angle = rotquat.to_axis_angle()
            # (important part is to negate the angle of the axis-angle)
            rotquat = Quaternion(rotquat_axis, -rotquat_angle)
            rotmtx = rotquat.to_matrix().to_4x4()
        else:
            rotmtx = Matrix.Identity(4)

        # calculate pose scale
        if scale is not None:
            sclx, scly, sclz = scale
            # Back when we were setting the rest pose, we couldn't set a rest scale.
            # So if this bone was supposed to have a rest scale, now we take that rest
            # scale and apply the inverse to this bone's pose scale, thereby achieving
            # the same effect.
            if restscale is not None:
                restsclx, restscly, restsclz = restscale
                sclx = sclx / restsclx
                scly = scly / restscly
                sclz = sclz / restsclz
            sclmtx_x = Matrix.Scale(sclx, 4, (1, 0, 0))
            sclmtx_y = Matrix.Scale(scly, 4, (0, 1, 0))
            sclmtx_z = Matrix.Scale(sclz, 4, (0, 0, 1))
            sclmtx = sclmtx_x @ sclmtx_y @ sclmtx_z
        else:
            sclmtx = Matrix.Identity(4)

        # combine position/rotation/scale into a Blender pose
        pose_matrix = posmtx @ rotmtx @ sclmtx

        return pose_matrix

    def _load_anims(self) -> None:
        animseps = self._xganimseps
        anim_name_num_digits = len(str(len(animseps) - 1))

        for anim_idx, animsep in enumerate(animseps):
            bpyarmobj = self._get_armature(mode="POSE")
            bpyarmobj.animation_data_create()

            # create a name for the new Action
            # Include model name in the Action name. Users can manually apply an Action
            # to any armature, so we want to make it clear which armature should have it
            prepend_model_name = f"{self._bl_name} - " if self._bl_name else ""
            anim_name = f"{anim_idx:0{anim_name_num_digits}}"
            bpyarmobj.animation_data.action = bpy.data.actions.new(
                f"{prepend_model_name}{anim_name}"
            )

            # load the animation data into the Action
            for xgkeyframeidx, bpyframenum in zip(
                animsep.keyframeidxs, animsep.actual_framenums
            ):
                self._load_anim_pose_frame(xgkeyframeidx, bpyframenum)

            bpyaction: Action = bpyarmobj.animation_data.action
            bpyaction.use_fake_user = True
            bpyaction.frame_end = animsep.playback_length

            # put this Action into a new NLA track/strip
            bpy_nla_track = bpyarmobj.animation_data.nla_tracks.new()
            bpy_nla_track.name = anim_name
            bpy_nla_strip = bpy_nla_track.strips.new(anim_name, 0, bpyaction)
            bpy_nla_strip.name = anim_name  # because it didn't stick the first time
            bpy_nla_strip.action_frame_end = animsep.playback_length
            # bpy_nla_strip.extrapolation = "NOTHING"  # nope, looks bad when anim loops

        if animseps:  # prevents case where bpyarmobj hasn't been defined yet
            # make first animation play by default
            nlatrack0 = bpyarmobj.animation_data.nla_tracks[0]
            nlatrack0.is_solo = True
            bpyarmobj.animation_data.action = None  # just tidier I guess

    def _load_anim_pose_frame(self, xg_keyframe, blender_frame) -> None:
        bpybonename_restscale = self._mappings.bpybonename_restscale
        bpyarmobj = self._get_armature(mode="POSE")
        bpy.context.scene.frame_set(blender_frame)

        for bonenode, bpybonename in chain(
            self._mappings.xgbone_bpybonename.items(),
            self._mappings.xgdagtransform_bpybonename.items(),
        ):
            if not hasattr(bonenode, "inputMatrix") or not bonenode.inputMatrix:
                continue

            # get the Blender posebone to be posed + the xgBgMatrix containing the pose
            bpyposebone = bpyarmobj.pose.bones[bpybonename]
            bpyposebone.rotation_mode = "QUATERNION"
            bgmatrixnode = bonenode.inputMatrix[0]

            # position the bone
            (
                pose_matrix,
                prs_is_animated,
            ) = self._calc_flattened_animpose_matrix_and_prs_is_animated(
                bgmatrixnode,
                xg_keyframe,
                restscale=bpybonename_restscale.get(bpybonename),
            )
            if self.debugoptions.correct_pose_axes:
                pose_matrix = _correct_pose_matrix_axes(pose_matrix)
            bpyposebone.matrix = pose_matrix

            # correct bpyposebone.rotation_quaternion to work with previous frame's
            rotquat = Quaternion(bpyposebone.rotation_quaternion)
            prevquat = self._mappings.bpybonename_previousquat.get(bpybonename)
            if prevquat is not None:
                rotquat.make_compatible(prevquat)
                bpyposebone.rotation_quaternion = rotquat
            self._mappings.bpybonename_previousquat[bpybonename] = rotquat

            # insert keyframes for this frame
            pos_is_animated, rot_is_animated, scl_is_animated = prs_is_animated
            if pos_is_animated:
                bpyposebone.keyframe_insert("location")
            if rot_is_animated:
                bpyposebone.keyframe_insert("rotation_quaternion")
            if scl_is_animated:
                bpyposebone.keyframe_insert("scale")

    def _calc_flattened_animpose_matrix_and_prs_is_animated(
        self,
        bgmatrixnode: XgBgMatrix,
        xg_keyframe: int,
        restscale: Optional[Tuple[float, float, float]] = None,
    ) -> Tuple[Matrix, Tuple[bool, bool, bool]]:
        """return the matrix of this bgmatrixnode multiplied by all its parents

        :param bgmatrixnode: XgBgMatrix node
        :param xg_keyframe: which XG keyframe's animation pose to calculate
        :param restscale: (x, y, z) or None. The rest scale this bone would have if
            Blender supported bone rest scale. Used to correct the pose scale.
        :return: Tuple of (flattened_pose_matrix, prs_is_animated) where prs_is_animated
            is a tuple of 3 bools, one each for pos/rot/scale being animated or not.
            The calling function can use these to decide which types of keyframes to
            insert this frame.
        """
        # initial pose PRS will be used where animation pose PRS does not exist
        (initial_pose_position, initial_pose_rotation, initial_pose_scale) = (
            bgmatrixnode.position,
            bgmatrixnode.rotation,
            bgmatrixnode.scale,
        )

        # figure out whether to animate each of position, rotation, scale this frame
        # as well as choosing the right position, rotation, and scale
        position_is_animated = rotation_is_animated = scale_is_animated = False
        if hasattr(bgmatrixnode, "inputPosition") and bgmatrixnode.inputPosition:
            if xg_keyframe < len(bgmatrixnode.inputPosition[0].keys):
                anim_pose_position = bgmatrixnode.inputPosition[0].keys[xg_keyframe]
                position_is_animated = True
            else:
                anim_pose_position = initial_pose_position
        else:
            anim_pose_position = initial_pose_position
        if hasattr(bgmatrixnode, "inputRotation") and bgmatrixnode.inputRotation:
            if xg_keyframe < len(bgmatrixnode.inputRotation[0].keys):
                anim_pose_rotation = bgmatrixnode.inputRotation[0].keys[xg_keyframe]
                rotation_is_animated = True
            else:
                anim_pose_rotation = initial_pose_rotation
        else:
            anim_pose_rotation = initial_pose_rotation
        if hasattr(bgmatrixnode, "inputScale") and bgmatrixnode.inputScale:
            if xg_keyframe < len(bgmatrixnode.inputScale[0].keys):
                anim_pose_scale = bgmatrixnode.inputScale[0].keys[xg_keyframe]
                scale_is_animated = True
            else:
                anim_pose_scale = initial_pose_scale
        else:
            anim_pose_scale = initial_pose_scale

        this_prs_is_animated = (
            position_is_animated,
            rotation_is_animated,
            scale_is_animated,
        )
        this_pose_matrix = self._calc_pose_matrix(
            anim_pose_position,
            anim_pose_rotation,
            anim_pose_scale,
            restscale=restscale,
        )
        if (
            hasattr(bgmatrixnode, "inputParentMatrix")
            and bgmatrixnode.inputParentMatrix
        ):
            (
                parent_pose_matrix,
                par_prs_is_animated,
            ) = self._calc_flattened_animpose_matrix_and_prs_is_animated(
                bgmatrixnode.inputParentMatrix[0], xg_keyframe
            )
            flat_prs_is_animated = _flatten_prs_is_animated(
                par_prs_is_animated, this_prs_is_animated
            )
            return parent_pose_matrix @ this_pose_matrix, flat_prs_is_animated
        else:
            return this_pose_matrix, (
                position_is_animated,
                rotation_is_animated,
                scale_is_animated,
            )

    def warn(self, message: str) -> None:
        """print warning message to console, store in internal list of warnings"""
        print(f"WARNING: {message}")
        self.warnings.append(message)
