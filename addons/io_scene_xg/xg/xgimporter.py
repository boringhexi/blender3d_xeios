"""xgimporter.py: import XgScene into Blender"""
import os.path
import re
from math import radians
from operator import neg
from typing import AnyStr, Collection, Dict, List, Optional, Sequence, Tuple, Union

import bpy
import mathutils
from bpy.types import Action
from bpy_extras.io_utils import unpack_list
from mathutils import Matrix, Quaternion, Vector

from .xganimsep import AnimSepEntry, read_animseps
from .xgerrors import XgImportError
from .xgscene import (
    Constants,
    XgBgMatrix,
    XgBone,
    XgDagMesh,
    XgDagTransform,
    XgMaterial,
    XgScene,
)
from .xgscenereader import XgSceneReader


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


class XgImporter:
    """imports an XgScene into Blender"""

    def __init__(
        self,
        xgscene: XgScene,
        texturedir: Optional[AnyStr] = None,
        xganimseps: Sequence[AnimSepEntry] = None,
        bl_name: str = "UNNAMED",
        global_import_scale: Optional[float] = None,
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
        self.warnings = []

        class ImporterOptions:
            def __init__(self):
                self.import_textures = texturedir is not None
                self.import_animations = xganimseps is not None

        class ImporterDebugOptions:
            def __init__(self):
                self.correct_mesh_axes = False
                self.correct_restpose_axes = False

        self.options = ImporterOptions()
        self.debugoptions = ImporterDebugOptions()

        if texturedir is None:
            self.warn("No texture directory provided, textures will not be imported")
        if xganimseps is None:
            self.warn("No animseps data provided, animations will not be imported")

        self._bpyemptyobj = None
        self._bpyarmatureobj = None

        # shortcut for the long function to add a created Blender object to the scene
        self._link_to_blender_func = None

        class Mappings:
            """holds relationships between XgScene data and Blender data"""

            def __init__(self):
                self.xgdagmesh_bpymeshobj: Dict[XgDagMesh, bpy.types.Object] = dict()
                self.xgdagtransform_bpybonename: Dict[XgDagTransform, str] = dict()
                self.xgbone_bpybonename: Dict[XgBone, str] = dict()
                self.xgbgmatrix_bpybonename: Dict[XgBgMatrix, str] = dict()
                self.regmatnode_bpymat: Dict[XgMaterial, bpy.types.Material] = dict()
                self.bpybonename_restscale: Dict[str, Vector] = dict()

        self._mappings = Mappings()

        # TODO Not yet used:
        if global_import_scale is None:
            global_import_scale = 1
        self._global_import_scale = global_import_scale
        gis = global_import_scale
        # matrix effect: rotates 90deg about X, scales -1.0 across X, scales by gis.
        #   In other words, it swaps from XG's coordinate system to Blender's (and
        #   scales as desired).
        self._global_import_mtx = Matrix(
            (
                [-gis, 0.00, 0.0, 0.0],
                [0.00, 0.00, gis, 0.0],
                [0.00, -gis, 0.0, 0.0],
                [0.00, 0.00, 0.0, 1.0],
            )
        )

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

    def _get_empty(self) -> bpy.types.Object:
        """return Blender Empty object, will be created if it doesn't exist yet"""
        if self._bpyemptyobj is None:
            bpyemptyobj = bpy.data.objects.new(self._bl_name, None)
            self._link_to_blender_func(bpyemptyobj)
            self._bpyemptyobj = bpyemptyobj
        else:
            bpyemptyobj = self._bpyemptyobj
        return bpyemptyobj

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
            self._link_to_blender_func(bpyarmobj)
            self._bpyarmatureobj = bpyarmobj
            bpyarmobj.parent = self._get_empty()  # parent armature to the Empty
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
        # shortcut for the long function to add a created Blender object to the scene
        self._link_to_blender_func = (
            bpy.context.view_layer.active_layer_collection.collection.objects.link
        )

        # back to object mode (in case we need to do armature stuff)
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        # 1) Initialize objects & set up hierarchy
        self._init_objs_hierarchy_from_dagnodes()

        # 3) Load regular materials
        # TODO this is currently done within _load_meshes()
        # if self._mappings.regmatnode_bpymat:
        #     self._load_regmaterials()

        # 5) Load meshes
        if self._mappings.xgdagmesh_bpymeshobj:
            self._load_meshes()

        # 6) Load armature bones
        if self._mappings.xgbone_bpybonename:
            self._load_bones()

        # 6.5 load pose
        if True:
            self._load_pose()

        # 7) Load animations
        if self.options.import_animations:
            self._load_animations()
            pass

        # back to object mode
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        # bpy.context.view_layer.objects.active = None #TODO nah, make the empty active

    def _init_objs_hierarchy_from_dagnodes(self):
        """create empty Blender objects and link them in the right hierarchy

        Create empty Blender objects and link them (e.g. assign textures to materials,
        parent bones to other bones). XgScene data will not be loaded into the Blender
        objects yet.
        """

        self._get_empty()  # create the Empty that will contain everything

        for dagnode, dagchildren in self._xgscene.dag.items():

            # For xgDagTransforms, create a bone to act as the transform, then
            # create child meshes and parent them to the bone.
            if dagnode.xgnode_type == "xgDagTransform":

                # init bone to use for the xgDagTransform
                bpybone_name = self._init_bone_hierarchy_from_bonenode(dagnode)

                # if bone was successfully created:
                if bpybone_name is not None:
                    # init meshes to be parented to the xgDagTransform's bone
                    bpymeshobjs = [
                        self._init_mesh_from_dagmeshnode(xgdagmesh, True)
                        for xgdagmesh in dagchildren
                    ]

                    # then parent meshes to the xgDagTransform
                    for bpymeshobj in bpymeshobjs:
                        # skip meshes that were not created
                        if bpymeshobj is not None:
                            bpymeshobj.parent = self._get_armature(mode="EDIT")
                            # TODO sloppy rn, but you must be in Pose mode when setting
                            #  a object bone parent, else you get those annoying errors
                            #  in the console
                            bpy.ops.object.mode_set(mode="POSE")
                            bpymeshobj.parent_type = "BONE"
                            bpymeshobj.parent_bone = bpybone_name
                            bpy.context.view_layer.update()  # TODO can't this wait?
                            bpymeshobj.matrix_world = Matrix()

                # if bone was not created:
                else:
                    # Skip the xgDagTransform since it will have no effect anyway.
                    # Just init the child xgDagMeshes.
                    for dagchild in dagchildren:
                        self._init_mesh_from_dagmeshnode(dagchild)

            # For xgDagMeshes, just create the mesh
            elif dagnode.xgnode_type == "xgDagMesh":
                self._init_mesh_from_dagmeshnode(dagnode)
                if dagchildren:
                    self.warn(
                        f"{dagnode} has dag children, this probably shouldn't happen? "
                        f"dag children {dagchildren} will not be loaded"
                    )

            # For other non-DAG node types, warn and skip
            else:
                self.warn(f"Unexpected node type {dagnode} in dag, skipping")

    def _init_bone_hierarchy_from_bonenode(
        self, bonenode: Union[XgDagTransform, XgBone]
    ) -> Optional[str]:
        """init a new Blender bone from bonenode, return Blender bone name

        Additional effects: also creates the parent bones, and their parents, all the
        way up the hierarchy, and parents them properly in Blender.

        :param bonenode: XgDagTransform or XgBone
        :return: Blender bone's name, or None if the bone was not created
            (either because bonenode is the wrong type, or because it has no inputMatrix
            which means it would have no effect)
        """
        if bonenode.xgnode_type == "xgDagTransform":
            bonename_mapping = self._mappings.xgdagtransform_bpybonename
        elif bonenode.xgnode_type == "xgBone":
            bonename_mapping = self._mappings.xgbone_bpybonename
        else:
            self.warn(
                f"tried to init {bonenode} as a bone, but it is not an XgDagTransform "
                "or XgBone, skipping"
            )
            return None

        if bonenode not in bonename_mapping:
            # initialize new Blender bone
            if hasattr(bonenode, "inputMatrix"):
                bpyarmobj = self._get_armature(mode="EDIT")
                bpybone_name = self._init_bone_from_bgmatrixnode(
                    bonenode.inputMatrix[0]
                )
                bpyeditbone = bpyarmobj.data.edit_bones[bpybone_name]
                cur_mtxnode, cur_bpybone = bonenode.inputMatrix[0], bpyeditbone
                # initialize new Blender bones all the way up the hierarchy
                while hasattr(cur_mtxnode, "inputParentMatrix"):
                    par_mtxnode = cur_mtxnode.inputParentMatrix[0]
                    par_bpybone_name = self._init_bone_from_bgmatrixnode(par_mtxnode)
                    par_bpyeditbone = bpyarmobj.data.edit_bones[par_bpybone_name]
                    cur_bpybone.parent = par_bpyeditbone
                    cur_mtxnode, cur_bpybone = par_mtxnode, par_bpyeditbone

            # bone has no inputMatrix, so don't bother
            else:
                if bonenode.xgnode_type == "xgBone":
                    self.warn(
                        f"{bonenode} has no inputMatrix, i.e. is a bone with a rest"
                        "pose but no animation or posing."
                    )
                return None

            bonename_mapping[bonenode] = bpybone_name
        else:
            # retrieve existing bone
            bpybone_name = bonename_mapping[bonenode]
        return bpybone_name

    def _init_bone_from_bgmatrixnode(self, bgmatrixnode: XgBgMatrix):
        """init a new Blender bone from bgmatrixnode, return Blender bone name

        Creates a new Blender bone from matrixnode if it hasn't been already, otherwise
        retrieves the existing Blender bone. Either way, returns the bone's name.

        :param bgmatrixnode: XgBgMatrix
        :return: name of Blender bone
        """
        bgmatrix_mapping = self._mappings.xgbgmatrix_bpybonename
        if bgmatrixnode not in bgmatrix_mapping:
            # create new Blender bone in armature

            bpyarmobj = self._get_armature(mode="EDIT")
            bpyeditbone = bpyarmobj.data.edit_bones
            bpyeditbone = bpyarmobj.data.edit_bones.new(name=bgmatrixnode.xgnode_name)
            bpyeditbone_name = bpyeditbone.name
            bgmatrix_mapping[bgmatrixnode] = bpyeditbone_name

            # default bone position (required for bones that parent meshes)
            # tail of (0,1,0) required to for xgDagTransform bones
            bpyeditbone.tail = (0, 1, 0)
            # TODO temporary armature view stuff for my convenience
            bpyarmobj.data.show_axes = True
            bpyarmobj.show_in_front = True
            # TODO also, maybe the mesh should be rotated in such a way that the bone
            #  position that gives a resting pose will be something more intuitive

            if bpyeditbone_name != bgmatrixnode.xgnode_name:
                self.warn(
                    f"xgbgmatrix {bgmatrixnode.xgnode_name!r} was given different name "
                    f"{bpyeditbone_name!r}"
                )
        else:
            # retrieve existing bone's name
            bpyeditbone_name = bgmatrix_mapping[bgmatrixnode]
        return bpyeditbone_name

    def _init_mesh_from_dagmeshnode(
        self, dagmeshnode: XgDagMesh, dagtransform: bool = False
    ) -> Optional[bpy.types.Object]:
        """create empty Blender mesh object or retrieve existing one, return it

        If xgdagmesh has not been encountered before, create an empty Blender mesh
        object for it and add it to the scene. Otherwise, grab the existing one.
        And if xgdagmesh isn't really an XgDagMesh, return None and don't create
        anything in Blender.

        Additional effects:
        Also creates mesh's material, link material to mesh #TODO should be moved out

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
            self._link_to_blender_func(bpymeshobj)
            bpymeshobj.parent = self._get_empty()  # parent mesh to the Empty
            mesh_mapping[dagmeshnode] = bpymeshobj

            # create material if it doesn't exist yet
            if hasattr(dagmeshnode, "inputMaterial"):
                matnode = dagmeshnode.inputMaterial[0]

                # if of type "xgMaterial", init as a regular material
                # if matnode.xgnode_type == "xgMaterial":
                if matnode.xgnode_type in ("xgMaterial", "xgMultiPassMaterial"):
                    # TODO for now, we handle regular mats and mpmats mostly the same.
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

                        # TODO init its texture, too.
                        if hasattr(matnode, "inputTexture"):

                            # TODO yeah, start thinking about doing init and loading at
                            #  the same time...
                            #  if not for anything else, then at least for this bs

                            # set up material nodes to use a texture
                            bpymat.use_nodes = True
                            bsdf = bpymat.node_tree.nodes["Principled BSDF"]
                            bpytex = bpymat.node_tree.nodes.new("ShaderNodeTexImage")
                            bpymat.node_tree.links.new(
                                bsdf.inputs["Base Color"], bpytex.outputs["Color"]
                            )

                            # texture image
                            # TODO yes, it's kind of early to do during hierarchy setup,
                            #  but there's no reason to defer loading...
                            # especially since check_existing will reuse already-loaded
                            # images by checking path

                            # Look for a likely PNG in the same dir based on texnode.url
                            texnode = matnode.inputTexture[0]
                            imagepath = _url_to_png(texnode.url, self._texturedir)
                            if imagepath is not None and self.options.import_textures:
                                # load it, and set it as the texture's image
                                bpyimage = bpy.data.images.load(
                                    imagepath, check_existing=True
                                )
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
                                bpyimage.filepath = os.path.join(
                                    self._texturedir, texnode.url
                                )
                                bpyimage.source = "FILE"
                            bpyimage.name = texnode.url
                            bpytex.image = bpyimage

                            # TODO: among other things, how do vertex colors work.
                            #  are they activated by xgmaterial settings or xgdagmesh
                            #  settings

                    else:
                        bpymat = self._mappings.regmatnode_bpymat[matnode]
                    # either way, add the material to this mesh
                    bpymeshdata.materials.append(bpymat)
                    bpymeshobj.active_material = bpymat

                # if of type "xgMultiPassMaterial", init all its inputmaterials as nodes
                elif matnode.xgnode_type == "xgMultiPassMaterial":
                    # TODO handling mpmats differently is not a thing yet.
                    self.warn(f"can't do mpmats yet, skipping {matnode}")
                    pass

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
                    self._init_bone_hierarchy_from_bonenode(envnode.inputMatrix1[0])

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
            # get the original rest pose (position, rotation, and scale)
            rmtx = bonenode.restMatrix
            rmatrixti = Matrix((rmtx[:4], rmtx[4:8], rmtx[8:12], rmtx[12:]))
            rmatrixti.transpose()
            rmatrixti.invert()
            restpos, restrot, restscl = rmatrixti.decompose()

            # get the blender edit bone we'll be setting the rest pose for
            bpyeditbone = bpyarmobj.data.edit_bones[bpybonename]

            uncorrected_bpyeditbone_matrix = (
                Matrix.Translation(restpos * self._global_import_scale)
                @ restrot.to_matrix().to_4x4()
            )

            if self.debugoptions.correct_restpose_axes:
                # set axis-corrected rest pose
                axis_correction = (
                    Matrix.Scale(-1, 4, Vector((1, 0, 0)))
                    @ Matrix.Rotation(radians(180), 4, "Z")
                    @ Matrix.Rotation(radians(90), 4, "X")
                )
                bpyeditbone.matrix = axis_correction @ uncorrected_bpyeditbone_matrix
            else:
                # set rest pose without correcting the axes
                bpyeditbone.matrix = uncorrected_bpyeditbone_matrix

            # rest scale: save for later
            # XG's rest poses can have rest scale, but Blender's can't. So later, we'll
            # use rest scale to adjust the pose scale, thereby achieving the same effect
            # (axis correction will happen then, not now)
            self._mappings.bpybonename_restscale[bpybonename] = restscl

            bpyeditbone.length = BONE_SIZE

    def _load_animations(self) -> None:
        """create actions, load animation data from the XG scene into Blender bones"""
        # bones need to have been positioned by now.
        #  at the very least, any given bone about to be animated should be at its
        #  final rest pose position. Maybe can get away with positioning its parents
        #  later...

        animseps = self._xganimseps
        if not animseps:
            return
        # TODO need editmode so it can get the edit_bone matrices. But I may end up
        #  storing that rest PRS stuff in a mapping so that no editmode is needed
        bpyarmobj = self._get_armature(mode="EDIT")
        bpyarmobj.animation_data_create()

        # create empty Blender animations in advance
        num_digits = len(str(len(animseps) - 1))
        anim_names = (f"{x:0{num_digits}}" for x in range(len(animseps)))
        nla_strips_by_idx = []
        have_set_solo = False  # TODO temporary for animation comparison
        for anim_name, animsep in zip(anim_names, animseps):
            bpy_nla_track = bpyarmobj.animation_data.nla_tracks.new()
            bpy_nla_track.name = anim_name
            bpyaction: Action = bpy.data.actions.new(anim_name)
            bpyaction.use_fake_user = True
            bpy_nla_strip = bpy_nla_track.strips.new(anim_name, 0, bpyaction)
            bpy_nla_strip.action_frame_end = animsep.playback_length
            nla_strips_by_idx.append(bpy_nla_strip)
            # TODO temporary for animation comparison
            if not have_set_solo:
                bpy_nla_track.is_solo = True
                have_set_solo = True
            # TODO also need to set NLA strips not to hold pose after animation ends

        # populate empty animations with keyframes
        for matrixnode, bpybonename in self._mappings.xgbgmatrix_bpybonename.items():
            # TODO need to account for times later, e.g. Flying O blinkenlights
            if hasattr(matrixnode, "times"):
                pass

            pos_interpolator = (
                matrixnode.inputPosition[0]
                if hasattr(matrixnode, "inputPosition")
                else None
            )
            rot_interpolator = (
                matrixnode.inputRotation[0]
                if hasattr(matrixnode, "inputRotation")
                else None
            )
            scl_interpolator = (
                matrixnode.inputScale[0] if hasattr(matrixnode, "inputScale") else None
            )
            rest_matrix = bpyarmobj.data.edit_bones[bpybonename].matrix
            rest_pos = rest_matrix.to_translation()
            rest_rot = rest_matrix.to_quaternion()
            rest_scl = self._mappings.bpybonename_restscale.get(bpybonename)

            for idx, animsep in enumerate(animseps):
                animsep_keyframe_interval = int(animsep.keyframe_interval)
                animsep_playback_length = int(animsep.playback_length)
                animsep_start_keyframe = int(animsep.start_keyframe_idx)
                animsep_end_keyframe = (
                    animsep_start_keyframe
                    + animsep_playback_length // animsep_keyframe_interval
                )
                animsep_framenums = range(
                    0, animsep_playback_length + 1, animsep_keyframe_interval
                )
                bpy_nla_strip = nla_strips_by_idx[idx]
                bpyaction = bpy_nla_strip.action

                # XYZ position anims
                if pos_interpolator is not None:
                    # if False:
                    #     poskeys_absolute = [Vector((-x, -z, y)) *
                    #     self._global_import_scale for x, y, z in
                    #     pos_interpolator.keys]
                    poskeys_uncorrected_absolute = [
                        Vector((x, y, z)) * self._global_import_scale
                        for x, y, z in pos_interpolator.keys
                    ]

                    # TODO rest rot affects pos diffs
                    poskeys_uncorrected_diff = [
                        v - rest_pos for v in poskeys_uncorrected_absolute
                    ]

                    rest_rot_inv = rest_rot.copy()
                    rest_rot_inv.invert()
                    for poskeydiff in poskeys_uncorrected_diff:
                        poskeydiff.rotate(rest_rot_inv)

                    interp_type = pos_interpolator.type
                    bpy_data_path = f'pose.bones["{bpybonename}"].location'
                    # process each axis X=0,Y=1,Z=2 separately
                    for axis_idx, axis_keys in enumerate(
                        zip(*poskeys_uncorrected_diff)
                    ):
                        fcurve = bpyaction.fcurves.new(
                            data_path=bpy_data_path, index=axis_idx
                        )
                        # only keyframes from this animsep
                        animsep_axis_keys = tuple(
                            axis_key
                            for keyframe_idx, axis_key in enumerate(axis_keys)
                            if animsep_start_keyframe
                            <= keyframe_idx
                            <= animsep_end_keyframe
                        )
                        fcurve.keyframe_points.add(len(animsep_axis_keys))
                        # add the keyframes and set their interpolation type
                        fcurve.keyframe_points.foreach_set(
                            "co", unpack_list(zip(animsep_framenums, animsep_axis_keys))
                        )
                        # (slow way until foreach_set supports enum/str values)
                        if interp_type == Constants.InterpolationType.NONE:
                            for keyframe_point in fcurve.keyframe_points:
                                keyframe_point.interpolation = "CONSTANT"
                        elif interp_type == Constants.InterpolationType.LINEAR:
                            for keyframe_point in fcurve.keyframe_points:
                                keyframe_point.interpolation = "LINEAR"

                # TODO common function for all interpolators?
                #  tbh not sure about vertex/texcoord/shape interps, but...
                # import_interpolator_to_action() needs...
                #  - interpnode,
                #    interpnode gives .keys, .type [.times, .targets]
                #  - bpybonename (for data_path string) that matches the matrixnode
                #    using this interpnode
                #  - animsep
                #    animsep gives us start_keyframe, end_keyframe to get the keys for
                #    this anim only
                #  - bpyaction (to add the fcurve to)

                # WXYZ quaternion rotation anims
                if rot_interpolator is not None:
                    # print(bpybonename)
                    # print(f"rest_rot: {rest_rot.to_euler()}")
                    # rot_keys_quats = tuple(map(xgquat2blender, rot_interpolator.keys))
                    # rot_keys_quats = [
                    #     Quaternion((w, x, y, z)) for w, x, y, z in
                    #     rot_interpolator.keys]
                    #
                    # # print(f"anim0:    {rot_keys_quats[0].to_euler()}")
                    # # print(f"anim0_r90:{rot_keys_quats[0].to_euler()}")
                    # rot_key_quats_diffs = [
                    #     rest_rot.rotation_difference(quat) for quat in rot_keys_quats
                    # ]
                    #
                    # # TODO temporary override, make all rot keys absolute
                    # rot_key_quats_diffs = [
                    #     Quaternion((w, x, y, z)) for w, x, y, z in
                    #     rot_interpolator.keys]
                    #
                    # for i, quat in enumerate(rot_key_quats_diffs[:]):
                    #
                    #     # this sort of works? with imprecise result
                    #     # axis, angle = quat.to_axis_angle()
                    #     # x,y,z = axis
                    #     # axis = Vector((-x,-z,y))
                    #     # e = Quaternion(axis, angle).to_euler("XZY")
                    #     # e.x, e.y, e.z = e.x + radians(90), e.z, -e.y
                    #
                    #     # okay, so this gets us to the off-by-right-angle rest pose,
                    #     # which isn't too shabby.
                    #     e = quat.to_euler("ZYX")
                    #     e = Euler((e.x + radians(90), -e.y, -e.z), "ZYX")
                    #     rot_key_quats_diffs[i] = e.to_quaternion()
                    #
                    #     # but how to get from that, or from wxyz, to nice anim poses?
                    #
                    #     pass

                    # okay, so getting absolute rots is easy
                    rotkeys_uncorrected_absolute = [
                        Quaternion((w, x, y, z)) for x, y, z, w in rot_interpolator.keys
                    ]

                    # but, well, getting differential rots is harder
                    # I mean, it works when all rest rots are 0...

                    # no rot, for testing
                    rotkeys_uncorrected_diff = [
                        Quaternion() for rabs in rotkeys_uncorrected_absolute
                    ]

                    # invert matrices. wrong when rest_rot is 0
                    for i, abs_rot in enumerate(rotkeys_uncorrected_absolute):

                        rrotm = rest_rot.to_matrix()
                        absrotm = abs_rot.to_matrix()

                        diffrot = (
                            rrotm.inverted() @ absrotm.inverted()
                        )  # um. I think this works
                        diffrot = (absrotm @ rrotm).inverted()  # this works too
                        diffquat2 = rest_rot.rotation_difference(
                            abs_rot.inverted()
                        )  # and this

                        diffquat = diffrot.to_quaternion()
                        rotkeys_uncorrected_diff[i] = diffquat
                        rotkeys_uncorrected_diff[i] = diffquat2

                    # TODO may not need this after axis correction & differential poses?
                    #  or maybe do this anyway for safety, idk
                    if len(rotkeys_uncorrected_diff) > 1:
                        for quat1, quat2 in zip(
                            rotkeys_uncorrected_diff, rotkeys_uncorrected_diff[1:]
                        ):
                            quat2.make_compatible(quat1)

                    interp_type = rot_interpolator.type
                    bpy_data_path = f'pose.bones["{bpybonename}"].rotation_quaternion'
                    # process each component W=0,X=1,Y=2,Z=3 separately
                    for axis_idx, axis_keys in enumerate(
                        zip(*rotkeys_uncorrected_diff)
                    ):
                        fcurve = bpyaction.fcurves.new(
                            data_path=bpy_data_path, index=axis_idx
                        )
                        # only keyframes from this animsep
                        animsep_axis_keys = tuple(
                            axis_key
                            for keyframe_idx, axis_key in enumerate(axis_keys)
                            if animsep_start_keyframe
                            <= keyframe_idx
                            <= animsep_end_keyframe
                        )
                        fcurve.keyframe_points.add(len(animsep_axis_keys))
                        # add the keyframes and set their interpolation type
                        fcurve.keyframe_points.foreach_set(
                            "co", unpack_list(zip(animsep_framenums, animsep_axis_keys))
                        )
                        # (slow way until foreach_set supports enum/str values)
                        if interp_type == Constants.InterpolationType.NONE:
                            for keyframe_point in fcurve.keyframe_points:
                                keyframe_point.interpolation = "CONSTANT"
                        elif interp_type == Constants.InterpolationType.LINEAR:
                            for keyframe_point in fcurve.keyframe_points:
                                keyframe_point.interpolation = "LINEAR"

                # XYZ scale anims
                if scl_interpolator is not None:
                    scl_keys = scl_interpolator.keys
                    # since Blender did not let us set a rest scale for this bone,
                    # adjust the pose scale to compensate for it
                    if rest_scl is not None:
                        scl_keys_restadjusted = [
                            Vector((x / rest_scl.x, y / rest_scl.y, z / rest_scl.z))
                            for x, y, z in scl_keys
                        ]
                    else:
                        scl_keys_restadjusted = [
                            Vector((x, y, z)) for x, y, z in scl_keys
                        ]

                    interp_type = scl_interpolator.type
                    bpy_data_path = f'pose.bones["{bpybonename}"].scale'
                    # process each axis X=0,Y=1,Z=2 separately
                    for axis_idx, axis_keys in enumerate(zip(*scl_keys_restadjusted)):
                        fcurve = bpyaction.fcurves.new(
                            data_path=bpy_data_path, index=axis_idx
                        )
                        # only keyframes from this animsep
                        animsep_axis_keys = tuple(
                            axis_key
                            for keyframe_idx, axis_key in enumerate(axis_keys)
                            if animsep_start_keyframe
                            <= keyframe_idx
                            <= animsep_end_keyframe
                        )
                        fcurve.keyframe_points.add(len(animsep_axis_keys))
                        # add the keyframes and set their interpolation type
                        fcurve.keyframe_points.foreach_set(
                            "co", unpack_list(zip(animsep_framenums, animsep_axis_keys))
                        )
                        # TODO (slow way until foreach_set supports enum/str values)
                        if interp_type == Constants.InterpolationType.NONE:
                            for keyframe_point in fcurve.keyframe_points:
                                keyframe_point.interpolation = "CONSTANT"
                        elif interp_type == Constants.InterpolationType.LINEAR:
                            for keyframe_point in fcurve.keyframe_points:
                                keyframe_point.interpolation = "LINEAR"
                        # Also consider this approach:
                        # old_interp_type =
                        #  context.user_preferences.edit.keyframe_new_interpolation_type
                        # context.user_preferences.edit.keyframe_new_interpolation_type
                        #  = 'LINEAR'
                        # # insert your keyframes
                        # context.user_preferences.edit.keyframe_new_interpolation_type
                        #  = old_interp_type
                # TODO Temporarily lengthen all animations, for anim comparison
                bpy_nla_strip.scale = 1.8

    def _load_pose(self):
        """pose the bones

        editbones must already be in rest position.
        this is not the same an animation pose or rest pose; this "default" pose is
        likely to get overwritten by an animation, but sometimes is not (e.g. Noren's
        feet are un-animated and need to be posed this way to match the animation)
        """
        bpybonename_restscale = self._mappings.bpybonename_restscale
        bpyarmobj = self._get_armature(mode="POSE")

        for bonenode, bpybonename in self._mappings.xgbone_bpybonename.items():
            if not hasattr(bonenode, "inputMatrix") or not bonenode.inputMatrix:
                continue

            # get the Blender posebone to be posed + the xgBgMatrix containing the pose
            bpyposebone = bpyarmobj.pose.bones[bpybonename]
            bpyposebone.rotation_mode = "QUATERNION"
            bgmatrixnode = bonenode.inputMatrix[0]

            # calculate pose position
            posmtx = Matrix.Translation(
                (c * self._global_import_scale for c in bgmatrixnode.position)
            )

            # calculate pose rotation
            rotx, roty, rotz, rotw = bgmatrixnode.rotation
            rotquat = Quaternion((rotw, rotx, roty, rotz))
            rotquat_axis, rotquat_angle = rotquat.to_axis_angle()
            # (important part is to negate the angle of the axis-angle)
            rotquat = Quaternion(rotquat_axis, -rotquat_angle)
            rotmtx = rotquat.to_matrix().to_4x4()

            # calculate pose scale
            sclx, scly, sclz = bgmatrixnode.scale
            # Back when we were setting the rest pose, we couldn't set a rest scale.
            # So if this bone was supposed to have a rest scale, now we take that rest
            # scale and apply the inverse to this bone's pose scale, thereby achieving
            # the same effect.
            if bpybonename in bpybonename_restscale:
                restsclx, restscly, restsclz = bpybonename_restscale[bpybonename]
                sclx = sclx / restsclx
                scly = scly / restscly
                sclz = sclz / restsclz
            sclmtx_x = Matrix.Scale(sclx, 4, (1, 0, 0))
            sclmtx_y = Matrix.Scale(scly, 4, (0, 1, 0))
            sclmtx_z = Matrix.Scale(sclz, 4, (0, 0, 1))

            # combine position/rotation/scale and apply to the posebone
            pose_matrix = posmtx @ rotmtx @ sclmtx_x @ sclmtx_y @ sclmtx_z
            bpyposebone.matrix = pose_matrix

    def warn(self, message: str) -> None:
        """print warning message to console, store in internal list of warnings"""
        print(f"WARNING: {message}")
        self.warnings.append(message)
