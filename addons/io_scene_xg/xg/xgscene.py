"""xgscene.py: classes that represent an XG file's scene graph


Usage:

Constants: Use this to interpret "magic" values used by various XgNode properties

new_xgnode: Use this function to create a blank XgNode of the defined name and type

XgNode: a Union type that represents all valid XgNode types. Use this in type hints

XgScene: Use this to initialize a new XgScene (including XgScene.from_path). Can also
    use this class in type hints

for better documentation of XG file contents, see:
http://gitaroopals.shoutwiki.com/wiki/.XG
"""
from inspect import get_annotations
from typing import Any, Collection, Dict, List, NamedTuple, Optional, Tuple, Union

from .xgerrors import XgSceneError


def _make_first_letter_lowercase(string: str) -> str:
    if not string:
        return string
    first_letter = string[0].lower()
    return first_letter + string[1:]


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
        """values used by xg*Interpolator.type (e.g. xgQuatInterpolator)

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


class Vertices(NamedTuple):
    # TODO really this should be a better class. with a len, and better way to indicate
    #  absent attribs than a blank list
    coords: Collection[Tuple[float, float, float]]
    normals: Collection[Tuple[float, float, float]]
    colors: Collection[Tuple[float, float, float, float]]
    texcoords: Collection[Tuple[float, float]]


class InputAttribute:
    # TODO contains nodename (or node?) and outputtype
    #  uhhh should I also include the inputattrib type somehow? hell idk
    pass


class XgBaseNode:
    """base class for all XG node types. A single node of an XG scene graph"""

    def __init__(self, name: Optional[str]):
        self._xgnode_name = name
        self._xgattributes: Dict[str, Any] = dict()

    @property
    def xgnode_name(self) -> Optional[str]:
        """name of this XgNode. can temporarily be None

        By the time xgscene.preadd_node is called on this node, it needs to have an
        actual str name instead of a None value.
        """
        return self._xgnode_name

    @xgnode_name.setter
    def xgnode_name(self, value: Optional[str]):
        self._xgnode_name = value

    @property
    def xgnode_type(self) -> str:
        """name of this XgNode's type, as would be seen inside an XG file"""
        return _make_first_letter_lowercase(self.__class__.__name__)

    def __repr__(self) -> str:
        """short representation to aid in debugging"""
        return f"<{self.xgnode_type}> {self.xgnode_name}"

    def set_property(self, name: str, value: Any) -> None:
        """add property to this XgNode

        Add a property. It can then be accessed as an instance attribute, e.g. x.name

        :param name: name of property, must be a valid XgNode property name
        :param value: property value
        :raise AttributeError if name isn't a valid property for this XgNode type
            (see class definitions of individual XgNode types)
        """
        if name in self._valid_property_names:
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
        :raise AttributeError if name isn't a valid input attribute for this XgNode type
            (see class definitions of individual XgNode types)
        """
        if inputtype in self._valid_input_attributes:
            if hasattr(self, inputtype):
                inputlist = getattr(self, inputtype)
            else:
                inputlist = []
                setattr(self, inputtype, inputlist)
            inputlist.append(input_xgnode)
        else:
            raise AttributeError(f"{inputtype!r} is not a valid input attribute name")

    @property
    def _valid_property_names(self) -> Tuple:
        """a tuple of valid property names for this class

        property and input attribute names come from type annotations in subclasses
        of this class. see XgBgGeometry for an example of properties and input
        attributes being defined
        """
        anno = get_annotations(
            self.__class__, globals=globals(), locals=locals(), eval_str=True
        )
        return tuple(
            varname
            for varname in anno.keys()
            if not (varname.startswith("_") or varname.startswith("input"))
        )

    @property
    def _valid_input_attributes(self) -> Tuple:
        """a tuple of valid input attribute names for this class"""
        anno = get_annotations(
            self.__class__, globals=globals(), locals=locals(), eval_str=True
        )
        return tuple(varname for varname in anno.keys() if varname.startswith("input"))

    @property
    def all_properties(self) -> Dict[str, Any]:
        """all properties of this node that have been set"""
        return {
            propname: propval
            for propname, propval in self.__dict__.items()
            if propname in self._valid_property_names
        }

    @property
    def all_inputattribs(self) -> Dict[str, List["XgNode"]]:
        """all input attributes of this node that have been set"""
        return {
            inputtype: inputlist
            for inputtype, inputlist in self.__dict__.items()
            if inputtype in self._valid_input_attributes
        }


class XgBgGeometry(XgBaseNode):
    density: float
    vertices: Vertices
    inputGeometry: Collection[InputAttribute]


class XgBgMatrix(XgBaseNode):
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]
    scale: Tuple[float, float, float]
    inputPosition: Collection[InputAttribute]
    inputRotation: Collection[InputAttribute]
    inputScale: Collection[InputAttribute]
    inputParentMatrix: Collection[InputAttribute]


class XgBone(XgBaseNode):
    restMatrix: Tuple[(float,) * 16]  # yes, 16 floats
    inputMatrix: Collection[InputAttribute]


class XgDagMesh(XgBaseNode):
    primType: int
    primCount: int
    primData: Collection[int]
    triFanCount: int
    triFanData: Collection[int]
    triStripCount: int
    triStripData: Collection[int]
    triListCount: int
    triListData: Collection[int]
    cullFunc: int
    inputGeometry: Collection[InputAttribute]
    inputMaterial: Collection[InputAttribute]


class XgDagTransform(XgBaseNode):
    inputMatrix: Collection[InputAttribute]


class XgEnvelope(XgBaseNode):
    startVertex: int
    weights: Tuple[Tuple[float, float, float, float], ...]
    vertexTargets: Collection[Tuple[int, ...]]
    inputMatrix1: Collection[InputAttribute]
    inputGeometry: Collection[InputAttribute]


class XgMaterial(XgBaseNode):
    blendType: int
    shadingType: int
    diffuse: Tuple[float, float, float, float]
    specular: Tuple[float, float, float, float]
    flags: int
    textureEnv: int
    uTile: int
    vTile: int
    inputTexture: Collection[InputAttribute]


class XgMultiPassMaterial(XgBaseNode):
    inputMaterial: Collection[InputAttribute]


class XgNormalInterpolator(XgBaseNode):
    type: int
    times: Collection[float]
    keys: Collection[Collection[Tuple[float, float, float]]]
    targets: Collection[int]
    inputTime: Collection[InputAttribute]


class XgQuatInterpolator(XgBaseNode):
    type: int
    times: Collection[float]
    keys: Collection[Tuple[float, float, float, float]]
    inputTime: Collection[InputAttribute]


class XgShapeInterpolator(XgBaseNode):
    type: int
    times: Collection[float]
    keys: Collection[Vertices]
    targets: Collection[int]
    inputTime: Collection[InputAttribute]


class XgTexCoordInterpolator(XgBaseNode):
    type: int
    times: Collection[float]
    keys: Collection[Collection[Tuple[float, float]]]
    targets: Collection[int]
    inputTime: Collection[InputAttribute]


class XgTexture(XgBaseNode):
    url: str
    mipmap_depth: int


class XgTime(XgBaseNode):
    numFrames: float
    time: float


class XgVec3Interpolator(XgBaseNode):
    type: int
    times: Collection[float]
    keys: Collection[Tuple[float, float, float]]
    inputTime: Collection[InputAttribute]


class XgVertexInterpolator(XgBaseNode):
    type: int
    times: Collection[float]
    keys: Collection[Collection[Tuple[float, float, float]]]
    targets: Collection[int]
    inputTime: Collection[InputAttribute]


_nodeclasses = (
    XgBgGeometry,
    XgBgMatrix,
    XgBone,
    XgDagMesh,
    XgDagTransform,
    XgEnvelope,
    XgMaterial,
    XgMultiPassMaterial,
    XgNormalInterpolator,
    XgQuatInterpolator,
    XgShapeInterpolator,
    XgTexCoordInterpolator,
    XgTexture,
    XgTime,
    XgVec3Interpolator,
    XgVertexInterpolator,
)
_nodenames_to_nodeclasses = {
    _make_first_letter_lowercase(cls.__name__): cls for cls in _nodeclasses
}

XgNode = Union[_nodeclasses]


def new_xgnode(nodename, nodetype) -> Union[_nodeclasses]:
    try:
        cls = _nodenames_to_nodeclasses[nodetype]
    except KeyError:
        raise XgSceneError(
            f"Cannot create {nodetype!r} {nodename!r}, unknown node type {nodetype!r}"
        )
    return cls(nodename)


class XgScene:
    """an XG scene graph

    About the Directed Acyclic Graph (DAG):
    Each XgScene contains a DAG. Only XgNodes in the DAG are actually used--
        it is possible for the file to contain unused nodes.
    The DAG is a hierarchy of XgNodes connected by input attributes, e.g.
        xgDagMesh.inputMaterial -> xgMaterial.inputTexture -> xgTexture.
    Only dag nodes (XgNodes of type 'xgDagTransform' and 'xgDagMesh') can
        be added directly to the DAG via add_dagnode(). All other nodes must
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
        won't be displayed in-game or imported into Blender), but DAG nodes pre-added
        this way can now be added to the DAG via add_dagnode. After adding, the new
        node can be retrieved with x.get_node(xgnode.xgnode_name).

        :param xgnode: instance of subclass of XgBaseNode
        :raises ValueError if xgnode has no name yet (i.e. None)
        """
        if xgnode.xgnode_name is None:
            raise ValueError(f"{xgnode} does not have a name yet, cannot pre-add")
        self._preadded_nodes[xgnode.xgnode_name] = xgnode

    def get_node(self, name) -> XgNode:
        """return the named XgNode, or raise LookupError if not found"""
        if name not in self._preadded_nodes:
            raise LookupError(f"XgScene contains no XgNode named {name!r}")
        return self._preadded_nodes[name]

    def add_dagnode(self, dagnode: XgNode, children: List[Optional[XgNode]]) -> None:
        """add `dagnode` & `children` to the XgScene's DAG

        Add `dagnode` and its child nodes to the scene's DAG (Directed Acyclic
        Graph), so that they will be displayed in-game or imported into Blender. If
        `dagnode` is already in the DAG, then `children` will be added to the children
        already in the DAG.

        :param dagnode: dag node, i.e. XgNode of type "xgDagTransform" or "xgDagMesh"
        :param children: iterable of XgNodes to be parented to dagnode, can be empty.
            (So far the only known case is xgDagMeshes as children of an xgDagTransform)
        :raises TypeError if `dagnode` or any `children` are not of type XgDagTransform
            or XgDagMesh
        :raises ValueError if 'dagnode' or any 'children' have not yet been pre-added
            via x.preadd_node().
        """
        # check for any non-dag nodes
        for node in (dagnode, *children):
            if node.xgnode_type not in ("xgDagTransform", "xgDagMesh"):
                raise TypeError(f"can't add {node!r} to DAG, it isn't a dag node")
        for node in (dagnode, *children):
            if node not in self.preadded_nodes.values():
                raise ValueError(
                    f"can't add {node!r} to DAG, it hasn't been pre-added yet"
                )

        # add nodes to the DAG
        if dagnode in self._dag:
            new_children = (c for c in children if c not in self._dag[dagnode])
            self._dag[dagnode].extend(new_children)
        else:
            self._dag[dagnode] = list(children)
