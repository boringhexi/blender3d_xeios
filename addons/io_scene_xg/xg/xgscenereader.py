"""xgscenereader.py: parse an XG file and return an xgscene.XgScene instance

For better documentation of XG file contents, see:
http://gitaroopals.shoutwiki.com/wiki/.XG
"""

from struct import unpack
from typing import BinaryIO, List, NamedTuple, Optional, Tuple, Union

from .xgerrors import XgInvalidFileError, XgReadError
from .xgscene import Vertices, XgScene, new_xgnode

DEBUG = False  # whether to print debug messages


def dbg(s):
    if DEBUG:
        print(s)


class XgSceneReader:
    """an XgSceneReader to read an XgScene from an XG file

    usage:
    xr = XgSceneReader(fileobj) or xr = XgSceneReader.from_path(filepath)
    scene = xr.read_xgscene()
    """

    def __init__(self, file: BinaryIO, autoclose: bool = False) -> None:
        """initialize an XgSceneReader from a file object

        :param file: a binary file object. The file position is expected to be at the
        beginning of the XG contents. File position after read_xgscene(), etc is not
        guaranteed.
        :param autoclose: if True, automatically close the file when done parsing, if an
        error is encountered, or when this XgSceneReader instance gets deleted
        """
        self._file = file
        self._autoclose = autoclose
        self._xgscene = XgScene()
        self._dbg_tokenpos = 0  # file position of last-read Pascal string
        # used as offset when raising XgReadError

    @classmethod
    def from_path(cls, filepath: str, autoclose: bool = False) -> "XgSceneReader":
        """initialize an XgSceneReader from an XG file path

        :param filepath: path to an XG file
        :param autoclose: if True, automatically close the file when done parsing, an
        error is encountered, or this XgSceneReader instance gets deleted
        :return: an XgSceneReader instance
        """
        file = open(filepath, "rb")
        return cls(file, autoclose=autoclose)

    def read_xgscene(self) -> XgScene:
        """read from the XG file, return an XgScene instance"""
        try:
            magic = self._file.read(4)
            if magic != b"XGBv":
                raise XgInvalidFileError(f"Not an XG file (unknown header {magic!r})")
            version = self._file.read(4)
            if version != b"1.00":
                raise XgInvalidFileError(f"Unknown file version {version!r}")

            dbg("=== BEGIN PARSING XG FILE ===")
            if hasattr(self._file, "name") and self._file.name:
                dbg(self._file.name)

            token = self._read_pstr()
            while token:
                if token != "dag":
                    nodetype = token
                    self._parse_xgnode(nodetype)
                    token = self._read_pstr()
                else:  # token == "dag"
                    self._parse_dag()
                    break

            if self._autoclose:
                self._file.close()
            return self._xgscene

        except Exception:
            if self._autoclose:
                self._file.close()
            raise

    def _parse_xgnode(self, nodetype: str) -> None:
        """parse a single XgNode of type nodetype from the XG file

        :param nodetype: the type of the XgNode to be read, such as "xgMaterial"
        """
        dbg_namepos = self._dbg_tokenpos  # position at which nodename was read
        nodename = self._read_pstr()
        token = self._read_pstr()

        if token == ";":  # node declaration (create new node)
            node = new_xgnode(nodename, nodetype)
            self._xgscene.preadd_node(node)
            dbg(f"Created and pre-added {node!r}")

        elif token == "{":  # node definition (update existing node)
            try:
                node = self._xgscene.get_node(nodename)
            except LookupError:
                raise XgReadError(
                    f"node {nodename!r} hasn't been declared yet", dbg_namepos
                )
            dbg(f"{node!r}:")
            token = self._read_pstr()
            while token != "}":
                dbg_propval = None  # property value to print in debug text

                # read property (single uint32)
                if token in (
                    "blendType",
                    "cullFunc",
                    "flags",
                    "mipmap_depth",
                    "primCount",
                    "primType",
                    "shadingType",
                    "startVertex",
                    "textureEnv",
                    "triFanCount",
                    "triListCount",
                    "triStripCount",
                    "type",
                    "uTile",
                    "vTile",
                ):
                    node.set_property(token, self._read_uint32())

                # read property (single float)
                elif token in ("density", "numFrames", "time"):
                    val = self._read_float32()
                    dbg_propval = format(val, ".2f")
                    node.set_property(token, val)

                # read property (3 floats)
                elif token in ("position", "scale"):
                    val = self._read_float32(3)
                    dbg_propval = "(" + ", ".join(format(x, ".2f") for x in val) + ")"
                    node.set_property(token, val)

                # read property (4 floats)
                elif token in ("diffuse", "rotation", "specular"):
                    val = self._read_float32(4)
                    dbg_propval = "(" + ", ".join(format(x, ".2f") for x in val) + ")"
                    node.set_property(token, val)

                # read property (list of uint32)
                elif token in (
                    "primData",
                    "triFanData",
                    "triListData",
                    "triStripData",
                    "targets",
                ):
                    size = self._read_uint32()
                    dbg_propval = f"<{size} items>"
                    node.set_property(token, self._read_uint32(size))

                # read property (float32 list)
                elif token == "times":
                    size = self._read_uint32()
                    dbg_propval = f"<{size} items>"
                    node.set_property(token, self._read_float32(size))

                # read property (list of 16 float32s)
                elif token == "restMatrix":
                    val = self._read_float32(16)
                    dbg_propval = f'({", ".join(format(x, ".2f") for x in val)})'
                    node.set_property(token, val)

                # read property (Pascal string)
                elif token == "url":
                    node.set_property(token, self._read_pstr())

                # read property (vertex targets)
                elif token == "vertexTargets":
                    val = self._read_vertextargets()
                    dbg_propval = f"<{len(val)} vertex targets>"
                    node.set_property(token, val)

                # read property (vertices)
                elif token == "vertices":
                    val = self._read_vertices()
                    words = [f"<{len(val.coords)} coords"]
                    for attrib in ("normals", "colors", "texcoords"):
                        words.append(", ")
                        if not getattr(val, attrib):
                            words.append("no ")
                        words.append(attrib)
                    words.append(">")
                    dbg_propval = "".join(words)
                    node.set_property(token, val)

                # read property (weights)
                elif token == "weights":
                    num_weights = self._read_uint32()
                    weights = [self._read_float32(4) for _ in range(num_weights)]
                    # TODO can read all at once and chunk afterwards
                    dbg_propval = f"<{num_weights} weights>"
                    node.set_property(token, weights)

                # read property (list of keys)
                elif token == "keys":
                    numkeys = self._read_uint32()
                    dbg_propval = f"<{numkeys} keys>"

                    # contents/size of each key depends on the nodetype
                    # TODO can read all at once and chunk afterwards
                    if nodetype == "xgVec3Interpolator":
                        keys = [self._read_float32(3) for _ in range(numkeys)]
                    elif nodetype == "xgQuatInterpolator":
                        keys = [self._read_float32(4) for _ in range(numkeys)]
                    elif nodetype == "xgTexCoordInterpolator":
                        keys = []
                        for x in range(numkeys):
                            size = self._read_uint32()
                            keys.append([self._read_float32(2) for _ in range(size)])
                    elif nodetype in ("xgVertexInterpolator", "xgNormalInterpolator"):
                        keys = []
                        for x in range(numkeys):
                            size = self._read_uint32()
                            keys.append([self._read_float32(3) for _ in range(size)])
                    elif nodetype == "xgShapeInterpolator":
                        keys = [self._read_vertices() for _ in range(numkeys)]
                    else:
                        raise XgReadError(
                            f"can't use 'keys' property with this nodetype: {node}",
                            self._dbg_tokenpos,
                        )
                    node.set_property(token, keys)

                # read input attribute
                elif token in (
                    "inputGeometry",
                    "inputMaterial",
                    "inputMatrix",
                    "inputMatrix1",
                    "inputParentMatrix",
                    "inputPosition",
                    "inputRotation",
                    "inputScale",
                    "inputTexture",
                    "inputTime",
                ):
                    inputnodename = self._read_pstr()
                    try:
                        inputNode = self._xgscene.get_node(inputnodename)
                    except LookupError:
                        raise XgReadError(
                            f"{node}.{token} uses nonexistent node {inputnodename!r}",
                            self._dbg_tokenpos,
                        )
                    outputAttrib = self._read_pstr()

                    node.append_inputattrib(token, inputNode, outputAttrib)
                    dbg_propval = f"{inputNode}, {outputAttrib}"

                # encountered unknown property or input attribute
                else:
                    raise XgReadError(
                        f"{node} has unknown property or input attribute {token!r}",
                        self._dbg_tokenpos,
                    )

                if dbg_propval is None:
                    dbg_propval = getattr(node, token)  # use original value
                dbg(f"  {token} = {dbg_propval}")

                token = self._read_pstr()  # next token

        else:
            raise XgReadError(
                f"(xgNode) expected ';' or '{{' but found {token!r}", self._dbg_tokenpos
            )

    def _parse_dag(self) -> None:
        """parse Directed Acyclic Graph from the XG file"""
        dbg("Adding DAG nodes to DAG:")
        token = self._read_pstr()
        if token != "{":
            raise XgReadError(
                f"(Dag) expected '{{' but found {token!r}", self._dbg_tokenpos
            )
        token = self._read_pstr()
        while token != "}":
            try:
                dagnode = self._xgscene.get_node(token)
            except LookupError:
                raise XgReadError(
                    f"(Dag) dag node {token!r} does not exist", self._dbg_tokenpos
                )
            children = []
            token = self._read_pstr()
            if token != "[":
                raise XgReadError(
                    f"(Dag) expected '[' but found {token!r}", self._dbg_tokenpos
                )
            token = self._read_pstr()
            while token != "]":
                try:
                    child = self._xgscene.get_node(token)
                except LookupError:
                    raise XgReadError(
                        f"(Dag) child node {token!r} does not exist", self._dbg_tokenpos
                    )
                children.append(child)
                token = self._read_pstr()
            self._xgscene.add_dagnode(dagnode, children)

            dbg(f"  {dagnode} {children}")

            token = self._read_pstr()

    def _read_vertextargets(self) -> List[Tuple[int, ...]]:
        """read and return a list of vertex targets

        Vertex targets link one mesh's verts to another mesh's verts. So if the 3rd
        tuple is (2, 5), the mesh's 3rd vertex is linked to the other mesh's 2nd and
        5th vertices (remember that indices are zero-based).
        """
        size = self._read_uint32()
        vtData = self._read_int32(size)
        # split vtData into sequences, using -1 as delimiter
        vertexTargets = []
        idx = idxEnd = 0
        for num in vtData:
            if num >= 0:
                idxEnd += 1
            else:  # num == -1:
                vertexTargets.append(vtData[idx:idxEnd])
                idx = idxEnd = idxEnd + 1
        return vertexTargets

    def _read_vertices(self) -> Vertices:
        """read and return a namedtuple of vertices

        Return vertices, an namedtuple with the attributes below. Each attribute is a
        list of tuples of floats, or an empty list if these vertices don't have
        that attribute.
          vertices.coords - list of vertex positions coordinates (X,Y,Z)
          vertices.normals - list of vertex normals (X,Y,Z)
          vertices.colors - list of colors in range 0.0 - 1.0 (R,G,B,A)
          vertices.texcoords - list of texture coordinates (U,V)
        """
        vertexFlags = self._read_uint32()
        hasCoords = bool(vertexFlags & 1)
        hasNormals = bool(vertexFlags & 2)
        hasColors = bool(vertexFlags & 4)
        hasTexCoords = bool(vertexFlags & 8)
        numVerts = self._read_uint32()
        stride = 4 * hasCoords + 3 * hasNormals + 4 * hasColors + 2 * hasTexCoords
        vData = self._read_float32(numVerts * stride)

        # deinterleave vData into lists of seperate vertex attributes
        # TODO slicing and chunking may be more efficient? premature optimization tho
        coords, normals, colors, texcoords = [], [], [], []
        idx = 0  # current position in vData
        for x in range(numVerts):
            if hasCoords:
                coords.append(vData[idx : idx + 3])  # ignore 4th coordinate
                idx += 4
            if hasNormals:
                normals.append(vData[idx : idx + 3])
                idx += 3
            if hasColors:
                colors.append(vData[idx : idx + 4])
                idx += 4
            if hasTexCoords:
                texcoords.append(vData[idx : idx + 2])
                idx += 2

        return Vertices(coords, normals, colors, texcoords)

    def _read_pstr(self) -> str:
        """read and return a Pascal string, assumes Shift-JIS encoding

        a Pascal string is 1 byte (length) followed by that many bytes (string)

        raises EOFError if end of file is encountered before the entire string is read
        """
        self._dbg_tokenpos = self._file.tell()
        bsize = self._file.read(1)
        if not bsize:
            raise EOFError("Tried to read a Pascal string, but already at end of file")
        size = ord(bsize)
        bstr = self._file.read(size)
        if len(bstr) != size:
            raise EOFError("Encountered end of file while reading a Pascal string")
        return bstr.decode(encoding="sjis")

    def _read_uint32(self, size: Optional[int] = None) -> Union[int, Tuple[int, ...]]:
        """read and return a unsigned 32-bit integer (little-endian)

        :param size: if specified, read this many integers and return as a tuple
        :return: an int or a tuple of ints
        """
        if size is None:
            return unpack("<I", self._file.read(4))[0]
        else:
            fmt = f"<{size:d}I"
            return unpack(fmt, self._file.read(4 * size))

    def _read_int32(self, size: Optional[int] = None) -> Union[int, Tuple[int, ...]]:
        """read and return a signed 32-bit integer (little-endian)

        :param size: if specified, read this many integers and return as a tuple
        :return: an int or a tuple of ints
        """
        if size is None:
            return unpack("<i", self._file.read(4))[0]
        else:
            fmt = f"<{size:d}i"
            return unpack(fmt, self._file.read(4 * size))

    def _read_float32(
        self, size: Optional[int] = None
    ) -> Union[float, Tuple[float, ...]]:
        """read and return a 32-bit float (little-endian)

        :param size: if specified, read this many floats and return as a tuple
        :return: a float or a tuple of floats
        """
        if size is None:
            return unpack("<f", self._file.read(4))[0]
        else:
            fmt = f"<{size:d}f"
            return unpack(fmt, self._file.read(4 * size))

    def __del__(self):
        if self._autoclose:
            self._file.close()
