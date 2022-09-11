"""xgexporter.py: export Blender objects to XgScene"""
from collections import defaultdict
from typing import Any, Collection, Optional, Tuple

from bpy.types import Armature, Context, Mesh, Object, PoseBone, VertexGroup
from bpy_extras.io_utils import unique_name
from mathutils import Matrix

from .xgscene import (
    XgBgGeometry,
    XgBgMatrix,
    XgBone,
    XgDagMesh,
    XgDagTransform,
    XgEnvelope,
    XgQuatInterpolator,
    XgScene,
    XgVec3Interpolator,
    XgTime,
)

_TOPLEVEL_EXPORTABLE_OBJECT_TYPES = ("MESH",)  # "TEXT")


class XgExporter:
    """exports objects out of Blender as an XgScene"""

    def __init__(self, global_export_scale=None, use_selection=True) -> None:
        """create an XgExporter to export Blender objects as an XgScene instance

        :param global_export_scale: value by which to scale exported meshes and armature
        :param use_selection: if True, export only selected objects from Blender
        """
        self.warnings = []

        self._xgscene = XgScene()
        self._use_selection = use_selection
        self._unique_name_dict = dict()  # used for _xg_unique_name()

        # create/access via self._get_xgtime()
        self._xgtime = None

        class Mappings:
            def __init__(self):
                self.bonekey_dagtransform = dict()
                self.posebone_bgmatrix = dict()

                # use _populate_which_bone_interpolators once the armature is known
                self.which_bone_interpolators = None

        self._mappings = Mappings()

        # TODO Not yet used:
        if global_export_scale is None:
            global_export_scale = 1
        self._global_import_scale = global_export_scale
        ges = global_export_scale
        # _global_export_mtx: scales -1.0 across X, rotates -90deg about X, scales by
        # ges. In other words, it swaps from Blender's coordinate system to XG's and
        # scales as desired.
        self._global_export_mtx = Matrix(
            (
                [-ges, 0.00, 0.0, 0.0],
                [0.00, 0.00, -ges, 0.0],
                [0.00, ges, 0.0, 0.0],
                [0.00, 0.00, 0.0, 1.0],
            )
        )

    def _xg_unique_name(self, key: Any, name: str) -> str:
        """return a unique name to be used for an xgNode

        :param key: one unique name is generated per key, so pass the xgNode here
        :param name: the returned name will be based on this
        :return: a unique name of length 255 or less, suitable for an xgNode
        """
        return unique_name(key, name, self._unique_name_dict, name_max=255, sep="_")

    def export_xgscene(self, context: Context) -> XgScene:
        """turn the current Blender scene or selection into an XgScene and return it

        :param context: current Blender context
        :return: the XgScene, ready to be written to file
        """
        blender_objects = self._get_blender_objects(context)
        self._init_xgnodes_hierarchy(blender_objects)
        # TODO more stuff, like actually writing data into the initialized xgnodes
        return self._xgscene

    def _get_blender_objects(self, context: Context) -> Collection[Object]:
        """return collection of Blender objects to be exported

        Will return all supported objects or just the selected ones, depending on
        whether `use_selection=True` was passed when this XgExporter was initialized.
        (Note that the export process exports both top-level objects and their relevant
        hierarchy. So if a mesh is animated by an armature, the armature also gets
        exported, if a mesh has a material and texture, those get exported too, etc.)

        :param context: the Blender context from which we can access Blender's objects
        :return: a collection of top-level Blender objects to be exported
        """
        blender_objects = [
            obj
            for obj in context.scene.objects
            if obj.visible_get() and obj.type in _TOPLEVEL_EXPORTABLE_OBJECT_TYPES
        ]
        if self._use_selection:
            blender_objects = [obj for obj in blender_objects if obj.select_get()]
        return blender_objects

    def _init_xgnodes_hierarchy(self, blender_objects: Collection[Object]) -> None:
        """create empty nodes in the XgScene and link them in the right hierarchy

        Create empty XgScene nodes (e.g. XgDagMesh) and link them in the right
        hierarchy, e.g. assign textures to materials, parent matrix nodes to other
        matrix nodes. (Blender data will not be loaded into the XgNodes yet.)

        :param blender_objects: collection of the top-level Blender objects whose
            hierarchies are to be initialized as XgScene nodes
        """
        for bobj in blender_objects:
            if bobj.type == "MESH":
                self._init_from_meshobj(bobj)

    def _init_from_meshobj(self, meshobj: Object) -> None:
        """initialize XgScene nodes from Blender Mesh object

        The Blender Mesh object and its relevant hierarchy (e.g. Armature, Material)
        will be used to initialize corresponding nodes in the XgScene

        :param meshobj: Blender Mesh object from which to initialize XgScene's nodes
        """
        xgscene, xg_unique_name = self._xgscene, self._xg_unique_name

        # Blender mesh object = xgDagMesh
        dagmeshnode = XgDagMesh(None)
        dagmeshnode.xgnode_name = xg_unique_name(dagmeshnode, meshobj.name)
        xgscene.preadd_node(dagmeshnode)
        # Blender bone parent of Blender mesh obj = xgDagTransform[xgDagMesh]
        if meshobj.parent_type == "BONE":
            dagtransformnode = self._init_dagtransform_from_parentbone(
                meshobj.parent, meshobj.parent_bone
            )
            xgscene.preadd_node(dagtransformnode)
            xgscene.add_dagnode(dagtransformnode, [dagmeshnode])
        # Blender mesh obj w/o bone parent = xgDagMesh[]
        else:
            xgscene.add_dagnode(dagmeshnode, [])

        # Blender mesh data = xgBgGeometry (xgDagMesh's inputGeometry)
        me: Mesh = meshobj.data
        bggeometrynode = XgBgGeometry(None)
        bggeometrynode.xgnode_name = xg_unique_name(bggeometrynode, me.name)
        xgscene.preadd_node(bggeometrynode)
        dagmeshnode.append_inputattrib("inputGeometry", bggeometrynode)

        # each Blender vertex group = an xgEnvelope (xgBgGeometry's inputGeometry)
        envelopenodes = self._init_envelopenodes_from_armature_vertexgroups(meshobj)
        for envelopenode in envelopenodes:
            bggeometrynode.append_inputattrib("inputGeometry", envelopenode)

        # TODO Blender mesh having material = xgMaterial or xgMultiPassMaterial
        #  Blender material having texture = xgTexture

        # TODO Blender mesh having vertex or texcoord animation =
        #  inputGeometry xgVertex/Normal/TexCoord/xgShapeInterpolator

    def _init_envelopenodes_from_armature_vertexgroups(
        self, meshobj: Object
    ) -> Collection[XgEnvelope]:
        """init and return XgEnvelope nodes from Blender Mesh object's vertex groups

        Using meshobj's vertex groups, initialize and return the corresponding
        XgEnvelope nodes. (The XgEnvelope nodes will be pre-added to the XgScene,
        but it is up to the caller to correctly link them to the XgBgGeometry nodes
        that use them.)

        If the mesh is not deformed by an Armature, the vertex groups will have no
        effect, therefore in that case no XgEnvelope nodes will be created or returned.

        :param meshobj: Blender Mesh object containing the vertex groups from which
            to initialize corresponding XgEnvelope nodes
        :return: collection of XgEnvelope nodes
        """

        # Don't use meshobj.find_armature(), we want armature modifier, not just parent
        # We do not handle multiple armatures (yet)
        for modifier in meshobj.modifiers:
            if modifier.type == "ARMATURE":
                armobj = modifier.object
                break
        else:
            return []

        xgscene, xg_unique_name = self._xgscene, self._xg_unique_name
        arm: Armature = armobj.data
        bone_names = [b.name for b in arm.bones]
        vertex_group: VertexGroup
        envelopenodes = []

        # each Blender vertex group + bone = an inputGeometry xgEnvelope
        for vertex_group in meshobj.vertex_groups:
            if vertex_group.name not in bone_names:
                # vertex group is not deformed by any bone, don't bother
                continue

            # create new xgEnvelope
            envelopenode = XgEnvelope(None)
            envelopenode.xgnode_name = xg_unique_name(envelopenode, vertex_group.name)
            xgscene.preadd_node(envelopenode)

            # and give it a blank inputGeometry xgBgGeometry
            envelope_bggeometry = XgBgGeometry(None)
            envelope_bggeometry.xgnode_name = xg_unique_name(
                envelope_bggeometry, vertex_group.name
            )
            xgscene.preadd_node(envelope_bggeometry)
            envelopenode.append_inputattrib("inputGeometry", envelope_bggeometry)

            # and give it a blank inputMatrix1 xgBone
            bonenode = XgBone(None)
            bonenode.xgnode_name = xg_unique_name(bonenode, vertex_group.name)
            xgscene.preadd_node(bonenode)
            envelopenode.append_inputattrib("inputMatrix1", bonenode)

            # and initialize that xgBone's inputMatrix xgBgMatrix (and its parents)
            self._populate_which_bone_interpolators(armobj)
            posebone = armobj.pose.bones[vertex_group.name]
            bonematrix = self._init_bgmatrix_from_posebone_recurse(posebone)
            bonenode.append_inputattrib("inputMatrix", bonematrix)

            envelopenodes.append(envelopenode)

        return envelopenodes

    def _init_dagtransform_from_parentbone(
        self, parent_armobj, parent_bonename
    ) -> XgDagTransform:
        """init and return the XgDagTransform corresponding to the bone in parent_armobj

        Initialize and return an XgDagTransform node corresponding to the parent bone
        (or retrieve the existing one if the corresponding XgDagTransform has already
        been created before this point). This XgDagTransform node will be pre-added to
        the XgScene, but it is up to the caller to correctly link it to the XgDagMesh it
        parents.

        :param parent_armobj: the Armature whose bone parents a mesh
        :param parent_bonename: the name of the bone that parents a mesh
        :return: the XgDagTransform node corresponding to the bone that was passed
        """
        # retrieve existing xgDagTransform if it was already initialized before
        # i.e. if multiple child xgDagMeshes are parented by it
        bonekey = (parent_armobj, parent_bonename)
        if bonekey in self._mappings.bonekey_dagtransform:
            return self._mappings.bonekey_dagtransform[bonekey]

        # otherwise, initialize a new one
        xg_unique_name = self._xg_unique_name
        dagtransformnode = XgDagTransform(None)
        dagtransformnode.xgnode_name = xg_unique_name(dagtransformnode, parent_bonename)
        self._mappings.bonekey_dagtransform[bonekey] = dagtransformnode

        # Blender pose bone + pose animation = inputMatrix xgBgMatrix
        parent_posebone = parent_armobj.pose.bones[parent_bonename]
        self._populate_which_bone_interpolators(parent_armobj)
        bgmatrixnode = self._init_bgmatrix_from_posebone_recurse(parent_posebone)
        dagtransformnode.append_inputattrib("inputMatrix", bgmatrixnode)

        return dagtransformnode

    def _init_bgmatrix_from_posebone_recurse(self, posebone: PoseBone) -> XgBgMatrix:
        """recursively go up the Blender bones to create and link xgBgMatrix nodes

        Initialize a XgBgMatrix node from posebone (or retrieve the existing one if
        it was already initialized before this point). In addition, if posebone has a
        parent PoseBone, a XgBgMatrix will be initialized (or retrieved) for that as
        well. The XgBgMatrix will be pre-added to the XgScene, but it is up to the
        caller to correctly link the XgBgMatrix to its parent (parent XgBgMatrix
        nodes created via recursion are automatically linked in this manner).

        :param posebone: Blender PoseBone from which to initialize a XgBgMatrix node
        :return: initialized XgBgMatrix node corresponding to posebone
        """
        # retrieve existing xgBgMatrix if it was already initialized before
        if posebone in self._mappings.posebone_bgmatrix:
            return self._mappings.posebone_bgmatrix[posebone]

        # otherwise, initialize a new one
        xgscene, xg_unique_name = self._xgscene, self._xg_unique_name
        bgmatrixnode = XgBgMatrix(None)
        bgmatrixnode.xgnode_name = xg_unique_name(bgmatrixnode, posebone.name)
        self._mappings.posebone_bgmatrix[posebone] = bgmatrixnode
        xgscene.preadd_node(bgmatrixnode)
        self._init_bgmatrix_interpolators(bgmatrixnode, posebone.name)

        # do the same for the parent bone if there's one
        if posebone.parent is not None:
            parent_bgmatrixnode = self._init_bgmatrix_from_posebone_recurse(
                posebone.parent
            )
            bgmatrixnode.append_inputattrib("inputParentMatrix", parent_bgmatrixnode)

        return bgmatrixnode

    def _populate_which_bone_interpolators(self, armobj: Object) -> None:
        """This MUST be run once prior to running _init_bgmatrix_interpolators.

        Populate self.mappings.which_bone_interpolators {Blender bone name: the
        animation interpolators it needs}. Each Blender bone that is animated will
        need corresponding position, rotation, or scale interpolator in the XgScene.

        :param armobj: Blender armature object containing the bones' animations
        """

        if self._mappings.which_bone_interpolators is not None:
            # mapping has already been created & populated
            return

        def which_interpolators_factory():
            class WhichInterpolators:
                def __init__(self):
                    self.has_pos = self.has_rot = self.has_scale = False

            return WhichInterpolators()

        wbi = defaultdict(which_interpolators_factory)
        if armobj.animation_data is not None:
            for nla_track in armobj.animation_data.nla_tracks:
                for strip in nla_track.strips:
                    for fcurve in strip.action.fcurves:
                        (
                            bpybonename,
                            interpolator_type,
                        ) = _bonename_propname_from_anim_data_path(fcurve.data_path)
                        if interpolator_type == "location":
                            wbi[bpybonename].has_pos = True
                        elif interpolator_type.startswith("rotation"):
                            wbi[bpybonename].has_rot = True
                        elif interpolator_type == "scale":
                            wbi[bpybonename].has_scale = True
        self._mappings.which_bone_interpolators = wbi

    def _init_bgmatrix_interpolators(
        self, bgmatrixnode: XgBgMatrix, bpybonename: str
    ) -> None:
        """init bgmatrixnode's animation interpolators and link them to bgmatrixnode

        For the XgBgMatrix, initialize its animation interpolators (position, rotation,
        and/or scale), and link them back to the XgBgMatrix.

        :param bgmatrixnode: XgBgMatrix node for which to initialize and link animation
            interpolators
        :param bpybonename: name of the Blender bone corresponding to this XgBgMatrix
        :raises RuntimeError if `self._populate_which_bone_interpolators` has not been
            run prior to running this
        """
        # and initialize any interpolators it will need
        xgscene, xg_unique_name = self._xgscene, self._xg_unique_name
        wbi = self._mappings.which_bone_interpolators
        if wbi is None:
            raise RuntimeError(
                "_populate_which_bone_interpolators has not been run yet"
            )
        which_interpolators = wbi[bpybonename]
        if which_interpolators.has_pos:
            posnode = XgVec3Interpolator(None)
            posnode.xgnode_name = xg_unique_name(posnode, bpybonename)
            xgscene.preadd_node(posnode)
            posnode.append_inputattrib("inputTime", self._get_xgtime())
            bgmatrixnode.append_inputattrib("inputPosition", posnode)
        if which_interpolators.has_rot:
            rotnode = XgQuatInterpolator(None)
            rotnode.xgnode_name = xg_unique_name(rotnode, bpybonename)
            xgscene.preadd_node(rotnode)
            rotnode.append_inputattrib("inputTime", self._get_xgtime())
            bgmatrixnode.append_inputattrib("inputRotation", rotnode)
        if which_interpolators.has_scale:
            scalenode = XgVec3Interpolator(None)
            scalenode.xgnode_name = xg_unique_name(scalenode, bpybonename)
            xgscene.preadd_node(scalenode)
            scalenode.append_inputattrib("inputTime", self._get_xgtime())
            bgmatrixnode.append_inputattrib("inputScale", scalenode)

    def _get_xgtime(self) -> XgTime:
        """initialize or retrieve the already-initialized XgTime

        A single XgTime node is to be used for all interpolators in the XgScene

        :return: this XgScene's sole XgTime node
        """
        xgscene, xg_unique_name = (
            self._xgscene,
            self._xg_unique_name,
        )
        if self._xgtime is None:
            xgtime = XgTime(None)
            xgtime.xgnode_name = xg_unique_name(xgtime, "time")
            xgscene.preadd_node(xgtime)
            self._xgtime = xgtime
        else:
            xgtime = self._xgtime
        return xgtime


def _bonename_propname_from_anim_data_path(data_path: str) -> Tuple[Optional[str], str]:
    """from an F-curve's data_path, return the bone name and property name

    e.g. from 'pose.bones["bonename"].property', return ("bonename", "property")

    :param data_path: data_path of an F-curve from
        armobj.animation_data.nla_tracks[i].strips[i].action.fcurves
    :return: tuple containing: name of the bone, name of the property being animated.
        properties can include: location, rotation_quaternion, rotation_euler[0/1/2],
        scale. If the data_path didn't start with "pose.bones..." as expected,
        the name of the bone will be None, and the name of the property will be the
        entire data_path.
    """
    if data_path.startswith('pose.bones["'):
        blob = data_path[len('pose.bones["') :]
        bonename_blob, propname = blob.rsplit(".", maxsplit=1)
        bonename = bonename_blob[: -len('"]')]
        return bonename, propname
    else:
        return None, data_path
