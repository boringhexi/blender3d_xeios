"""xgscene.py: classes that represent an XG file's scene graph

for better documentation of XG file contents, see:
http://gitaroopals.shoutwiki.com/wiki/.XG
"""
from typing import Any, Dict, List, Optional


class Constants:
    """Constants that certain types of XgNode use for certain properties"""

    class CullFunc:
        """values used by xgDagMesh.cullFunc

        TWOSIDED: All triangles are drawn double-sided
        CCWFRONT: Counter-clockwise triangles are front-facing
        CWFRONT: Clockwise triangles are front-facing
        """

        TWOSIDED = 0
        CCWFRONT = 1
        CWFRONT = 2

    class PrimType:
        """values used by xgDagMesh.primType

        How vertex indices are stored in xgDagMesh.triFanData/triStripData/triListData

        KICKSEP: (kick separately) for each primitive, there is a number followed by
        that many vertex indices

        KICKGROUP: (kick as groups) begins with a staring vertex index; then for each
        primitive, there is a number of vertices to use from the starting index (or from
        the last index of the previous primitive)
        """

        KICKSEP = 4
        KICKGROUP = 5

    class InterpolationType:
        """ values used by xg*Interpolator.type (e.g. xgQuatInterpolator)

        NONE - No interpolation between keyframes
        LINEAR - Linear interpolation between keyframes
        """

        NONE = 0
        LINEAR = 1

    class BlendType:
        """values used by xgMaterial.blendType

        Options ignore alpha (transparency) unless otherwise specified.

        MIX - Draw solid *
        ADD - Add to background (uses alpha)
        MULTIPLY - Multiply by background
        SUBTRACT - Subtract from background
        UNKNOWN - Very dark, almost black *
        MIXALPHA - Draw with alpha

        * same as MIXALPHA when Flags.USEALPHA is also enabled
        """

        MIX = 0
        ADD = 1
        MULTIPLY = 2
        SUBTRACT = 3
        UNKNOWN = 4
        MIXALPHA = 5

    class Flags:
        """values used by xgMaterial.flags, can be ORed together

        USEALPHA: Use the texture's alpha for transparency. This only affects appearance
        in certain cases (see XgNode.BlendType for details).
        """

        USEALPHA = 1

    class ShadingType:
        """values used by xgMaterial.shadingType

        UNSHADED - No shading, full brightness
        SHADED1 - Shaded (identical to SHADED2?)
        SHADED2 - Shaded
        VCOL_UNSHADED - Unshaded & uses vertex colors
        VCOL_SHADED - Shaded & uses vertex colors
        """

        UNSHADED = 0
        SHADED1 = 1
        SHADED2 = 2
        VCOL_UNSHADED = 3
        VCOL_SHADED = 4

    class TextureEnv:
        """values used by xgMaterial.textureEnv

        UV - Use model's texture coordinates
        SPHEREMAP - Reflective environment map
        """

        UV = 0
        SPHEREMAP = 1


class XgNode:
    """a single node of an XG scene graph

    an XgNode instance contains numerous other instance variables, divided into
    "properties" and "input attributes". A full documentation of properties and
    instance attributes is outside the scope of this script.
    """

    def __init__(self, name: str, nodetype: str) -> None:
        """create an empty XgNode with the given name and nodetype

        :param name: name of this XgNode
        :param nodetype: type of this XgNode, e.g. "xgMaterial"
        """
        self._xgnode_name = name
        self._xgnode_type = nodetype

        # Below are all the valid XgNode property and input attribute names:

        # xgBgGeometry
        self.density = None
        self.vertices = None
        self.inputGeometry: List["XgNode"] = []

        # xgBgMatrix
        self.position = None
        self.rotation = None
        self.scale = None
        self.inputPosition: List["XgNode"] = []
        self.inputRotation: List["XgNode"] = []
        self.inputScale: List["XgNode"] = []
        self.inputParentMatrix: List["XgNode"] = []

        # xgBone
        self.restMatrix = None
        self.inputMatrix: List["XgNode"] = []

        # xgDagMesh
        self.primType = None
        self.primCount = None
        self.primData = None
        self.triFanCount = None
        self.triFanData = None
        self.triStripCount = None
        self.triStripData = None
        self.triListCount = None
        self.triListData = None
        self.cullFunc = None
        self.inputGeometry: List["XgNode"] = []
        self.inputMaterial: List["XgNode"] = []

        # xgDagTransform
        self.inputMatrix: List["XgNode"] = []

        # xgEnvelope
        self.startVertex = None
        self.weights = None
        self.vertexTargets = None
        self.inputMatrix1: List["XgNode"] = []
        self.inputGeometry: List["XgNode"] = []

        # xgMaterial
        self.blendType = None
        self.shadingType = None
        self.diffuse = None
        self.specular = None
        self.flags = None
        self.textureEnv = None
        self.uTile = None
        self.vTile = None
        self.inputTexture: List["XgNode"] = []

        # xgMultiPassMaterial
        self.inputMaterial: List["XgNode"] = []

        # xgNormalInterpolator
        self.type = None
        self.times = None
        self.keys = None
        self.targets = None
        self.inputTime: List["XgNode"] = []

        # xgQuatInterpolator
        self.type = None
        self.keys = None
        self.inputTime: List["XgNode"] = []

        # xgShapeInterpolator
        self.type = None
        self.times = None
        self.keys = None
        self.inputTime = None

        # xgTexCoordInterpolator
        self.type = None
        self.times = None
        self.keys = None
        self.targets = None
        self.inputTime: List["XgNode"] = []

        # xgTexture
        self.url = None
        self.mipmap_depth = None

        # xgTime
        self.numFrames = None
        self.time = None

        # xgVec3Interpolator
        self.type = None
        self.keys = None
        self.inputTime: List["XgNode"] = []

        # xgVertexInterpolator
        self.type = None
        self.times = None
        self.keys = None
        self.targets = None

        self._valid_property_names = {
            name
            for name in self.__dict__.keys()
            if not (name.startswith("_") or name.startswith("input"))
        }
        self._valid_inputattrib_names = {
            name for name in self.__dict__.keys() if name.startswith("input")
        }

    @property
    def xgnode_name(self) -> str:
        """name of this XgNode"""
        return self._xgnode_name

    @property
    def xgnode_type(self) -> str:
        """type of this XgNode (e.g. "xgMaterial")"""
        return self._xgnode_type

    def __repr__(self) -> str:
        """short representation to aid in debugging"""
        return f"<{self.xgnode_type}> {self.xgnode_name}"

    def set_property(self, name: str, value: Any) -> None:
        """add property to this XgNode

        Add a property. It can then be accessed as an instance attribute, e.g. x.name

        :param name: name of property, must be a valid XgNode property name
        :param value: property value
        :raise AttributeError if name is one of the valid XgNode property names that was
            defined in XgNode.__init__
        """
        if name in self._valid_property_names and hasattr(self, name):
            setattr(self, name, value)
        else:
            raise AttributeError(f"{name!r} is not a valid XgNode property")

    def append_inputattrib(
        self, inputtype: str, input_xgnode: "XgNode", outputtype: str = ""
    ) -> None:
        """add an input attribute of inputtype, appending to the list of existing ones

        Example:
        mynode.append_inputattrib("inputMaterial", node1, "outputMaterial")
            will result in
            mynode.inputMaterial == [node1]
        Subsequently calling
        mynode.append_inputattrib("inputMaterial", node2, "outputMaterial")
            will then result in
            mynode.inputMaterial == [node1, node2]

        :param inputtype: name of the input attribute. e.g. "inputMaterial", must be a
            valid XgNode input attribute name
        :param input_xgnode: an XgNode instance appropriate to the the inputattrib. e.g.
            for inputattrib "inputMaterial", this should be an XgNode of nodetype
            "xgMaterial"
        :param outputtype: purpose unknown, currently unused
        :raise AttributeError if inputtype is not one of the valid input attribute names
            defined in XgNode.__init__
        """
        if inputtype in self._valid_inputattrib_names:
            inputlist = getattr(self, inputtype)
            inputlist.append(input_xgnode)
        else:
            raise AttributeError(f"{inputtype!r} is not a valid input attribute name")


class XgScene:
    """an XG scene graph

    About the Directed Acyclic Graph (DAG):
    Each XgScene contains a DAG. Only XgNodes in the DAG are actually used--
        it is possible for the file to contain unused nodes.
    The DAG is a hierarchy of XgNodes connected by input attributes, e.g.
        xgDagMesh.inputMaterial -> xgMaterial.inputTexture -> xgTexture.
    Only dag nodes (XgNodes of type 'xgDagTransform' and 'xgDagMesh') can
        added directly to the DAG via add_dagnode(). All other nodes must
        be connected to these nodes by input attributes.
    """

    def __init__(self) -> None:
        """create an empty XgScene"""
        # Directed Acyclic Graph {dagnode: [dagnodes]}
        self._dag: Dict[XgNode, List[Optional[XgNode]]] = dict()
        # pool of all pre-added nodes {XgNode name: XgNode}
        self._preadded_nodes: Dict[str, XgNode] = dict()

    @property
    def dag(self) -> Dict[XgNode, List[Optional[XgNode]]]:
        """the scene's DAG (directed acyclic graph)

        The returned dict's entries are {dag node: list of child nodes}
        """
        return self._dag

    @property
    def preadded_nodes(self) -> Dict[str, XgNode]:
        """return dict containing all XgNodes in the scene

        The returned dict's entries are {XgNode's name: XgNode}
        """
        return self._preadded_nodes

    def preadd_node(self, xgnode: XgNode) -> None:
        """pre-add XgNode node to the scene

        Pre-add an XgNode to the scene. It isn't part of the DAG yet (and therefore
        won't be imported), but it can now be added to the DAG via add_dagnode.
        After adding, the new node can be retrieved with x.get_node(xgnode.xgnode_name).
        """
        self._preadded_nodes[xgnode.xgnode_name] = xgnode

    def get_node(self, name) -> XgNode:
        """return the named XgNode, or raise LookupError if not found"""
        if name not in self._preadded_nodes:
            raise LookupError(f"XgScene contains no XgNode named {name!r}")
        return self._preadded_nodes[name]

    def add_dagnode(self, dagnode: XgNode, children: List[Optional[XgNode]]) -> None:
        """add dagnode & children to DAG, or raise ValueError if they aren't dag nodes

        Add dagnode and its child nodes to the scene's DAG (Directed Acyclic
            Graph). Only dag nodes are valid arguments; non-dag nodes will
            cause a ValueError to be raised. (See XgScene's class documentation
            text for more information.)
        Note: dagnode & its children should have already been added with preadd_node().

        dagnode: dag node, i.e. XgNode of type "xgDagTransform" or "xgDagMesh"
        children: list of XgNodes (or an empty list) to be parented to dagnode.
            (So far the only known case is xgDagMeshes as children of an xgDagTransform)
        """
        # check for any non-dag nodes
        dagtypes = ("xgDagTransform", "xgDagMesh")
        non_dagnode = None
        if dagnode.xgnode_type not in dagtypes:
            non_dagnode = dagnode
        else:
            for chnode in children:
                if chnode.xgnode_type not in dagtypes:
                    non_dagnode = chnode
                    break
        if non_dagnode is not None:
            raise ValueError(f"cannot add dag node, {non_dagnode!r} isn't a dag node")

        # add nodes to the DAG
        self._dag[dagnode] = children
