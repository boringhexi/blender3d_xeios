"""xgscenewriter.py: write an xgscene.XgScene instance to file

For better documentation of XG file contents, see:
http://gitaroopals.shoutwiki.com/wiki/.XG
"""
from itertools import chain, zip_longest
from struct import pack
from typing import BinaryIO, Collection, Iterable, List, NamedTuple, Tuple

from .xgerrors import XgWriteError
from .xgscene import XgScene, Vertices

DEBUG = False  # whether to print debug messages


def dbg(s):
    if DEBUG:
        print(s)


inputattrib_to_outputattrib = {
    "inputGeometry": "outputGeometry",
    "inputMatrix": "outputMatrix",
    "inputMatrix1": "envelopeMatrix",
    "inputMaterial": "outputMaterial",
    "inputPosition": "outputVec3",
    "inputRotation": "outputQuat",
    "inputScale": "outputVec3",
    "inputParentMatrix": "outputMatrix",
    "inputTexture": "outputTexture",
    "inputTime": "outputTime",
}


class XgSceneWriter:
    """an XgSceneWriter to write an XgScene to an XG file

    usage:
    xw = XgSceneWriter(fileobj) or xw = XgSceneWriter.from_path(filepath)
    xw.write_xgscene(my_xgscene)
    """

    def __init__(self, file: BinaryIO, autoclose: bool = True) -> None:
        """initialize an XgSceneWriter from a file object

        :param file: a binary file object. XG contents will be written to the current
        file position onward. File position after write_xgscene() will be at the end of
        the written contents
        :param autoclose: if True, automatically close the file when done parsing, if an
        error is encountered, or when this XgSceneWriter instance gets deleted
        """
        self._file = file
        self._autoclose = autoclose

    @classmethod
    def from_path(cls, filepath: str, autoclose: bool = True) -> "XgSceneWriter":
        """initialize an XgSceneWriter from an XG file path

        :param filepath: path to which to write an XG file
        :param autoclose: if True, automatically close the file when done writing, an
        error is encountered, or this XgSceneWriter instance gets deleted
        :return: an XgSceneWriter instance
        """
        file = open(filepath, "wb")
        return cls(file, autoclose=autoclose)

    def write_xgscene(self, xgscene: XgScene) -> int:
        """write xgscene to this XgSceneWriter's file

        :param xgscene: XgScene to write out to the file
        :return: the number of bytes written to file
        """
        try:
            num_bytes = self._write_header()
            num_bytes += self._write_xgnode_declarations(xgscene)
            num_bytes += self._write_xgnodes(xgscene)
            num_bytes += self._write_dagsetup(xgscene)

            if self._autoclose:
                self._file.close()
            return num_bytes
        except Exception:
            if self._autoclose:
                self._file.close()
            raise

    def _write_header(self):
        """writes the XG file header to file

        :return: number of bytes written to file (always 8)
        """
        header = b"XGBv1.00"
        self._file.write(header)
        return len(header)

    def _write_xgnode_declarations(self, xgscene: XgScene) -> int:
        """write all declarations of xgscene's nodes to file

        :param xgscene: XgScene whose XgNodes need declarations written to file
        :return: the number of bytes written to file
        """
        num_bytes = 0
        for xgnode in xgscene.preadded_nodes.values():
            num_bytes += self._write_pstr(xgnode.xgnode_type)
            num_bytes += self._write_pstr(xgnode.xgnode_name)
            num_bytes += self._write_pstr(";")
        return num_bytes

    def _write_xgnodes(self, xgscene: XgScene) -> int:
        """write all of xgscene's nodes to file

        :param xgscene: XgScene whose XgNodes need to be written to file
        :return: the number of bytes written to file
        """
        num_bytes = 0
        for xgnode in xgscene.preadded_nodes.values():
            num_bytes += self._write_pstr(xgnode.xgnode_type)
            num_bytes += self._write_pstr(xgnode.xgnode_name)
            num_bytes += self._write_pstr("{")
            for propname, propval in xgnode.all_properties.items():
                num_bytes += self._write_pstr(propname)

                # write property (single uint32)
                if propname in (
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
                    num_bytes += self._write_uint32(propval)

                # write property (single float)
                elif propname in ("density", "numFrames", "time"):
                    num_bytes += self._write_float32(propval)

                # write property (3 floats)
                elif propname in ("position", "scale"):
                    num_bytes += self._write_float32(*propval)

                # write property (4 floats)
                elif propname in ("diffuse", "rotation", "specular"):
                    num_bytes += self._write_float32(*propval)

                # write property (list of uint32)
                elif propname in (
                    "primData",
                    "triFanData",
                    "triListData",
                    "triStripData",
                    "targets",
                ):
                    num_bytes += self._write_uint32(len(propval))
                    num_bytes += self._write_uint32(*propval)

                # write property (list of float32)
                elif propname == "times":
                    num_bytes += self._write_uint32(len(propval))
                    num_bytes += self._write_float32(*propval)

                # write property (16 floats)
                elif propname == "restMatrix":
                    num_bytes += self._write_float32(*propval)

                # write property (Pascal string)
                elif propname == "url":
                    num_bytes += self._write_pstr(propval)

                # write property (vertex targets)
                elif propname == "vertexTargets":
                    num_bytes += self._write_vertextargets(propval)

                # write property (vertices)
                elif propname == "vertices":
                    num_bytes += self._write_vertices(propval)

                # write property (weights, each weight is 4 floats)
                elif propname == "weights":
                    num_bytes += self._write_uint32(len(propval))
                    num_bytes += self._write_float32(*chain.from_iterable(propval))

                # write property (list of keys)
                elif propname == "keys":
                    num_bytes += self._write_uint32(len(propval))

                    # contents/size of each key depends on the nodetype
                    nodetype = xgnode.xgnode_type

                    # key is 3 floats:
                    if nodetype == "xgVec3Interpolator":
                        num_bytes += self._write_float32(*chain.from_iterable(propval))

                    # key is 4 floats:
                    elif nodetype == "xgQuatInterpolator":
                        num_bytes += self._write_float32(*chain.from_iterable(propval))

                    # key is a sized list of 2-floats:
                    elif nodetype == "xgTexCoordInterpolator":
                        for key in propval:
                            num_bytes += self._write_uint32(len(key))
                            num_bytes += self._write_float32(*chain.from_iterable(key))

                    # key is a sized list of 3-floats:
                    elif nodetype in ("xgVertexInterpolator", "xgNormalInterpolator"):
                        for key in propval:
                            num_bytes += self._write_uint32(len(key))
                            num_bytes += self._write_float32(*chain.from_iterable(key))

                    # key is a Vertices:
                    elif nodetype == "xgShapeInterpolator":
                        for key in propval:
                            num_bytes += self._write_vertices(key)

                    else:
                        raise XgWriteError(
                            f"can't use 'keys' property with this nodetype: {xgnode}"
                        )

            for inputtype, inputlist in xgnode.all_inputattribs.items():
                for inputnode in inputlist:
                    num_bytes += self._write_pstr(inputtype)
                    num_bytes += self._write_pstr(inputnode.xgnode_name)
                    outputattrib = inputattrib_to_outputattrib[inputtype]
                    num_bytes += self._write_pstr(outputattrib)

            num_bytes += self._write_pstr("}")

        return num_bytes

    def _write_dagsetup(self, xgscene: XgScene) -> int:
        """write xgscene's DAG setup to this XgSceneWriter's file

        :param xgscene: XgScene whose DAG setup to write out to the file
        :return: the number of bytes written to file
        """
        num_bytes = self._write_pstr("dag")
        num_bytes += self._write_pstr("{")
        for dagnode, childnodes in xgscene.dag.items():
            num_bytes += self._write_pstr(dagnode.xgnode_name)
            num_bytes += self._write_pstr("[")
            for child_dagnode in childnodes:
                num_bytes += self._write_pstr(child_dagnode.xgnode_name)
            num_bytes += self._write_pstr("]")
        num_bytes += self._write_pstr("}")
        return num_bytes

    def _write_vertextargets(self, vertextargets: Iterable[Iterable[int]]) -> int:
        """write an iterable of vertex targets to file

        :param vertextargets: an iterable of iterables containing ints. Vertex targets
        link one set of vertices to another set of vertices. So for example, if the 3rd
        iterable is (2, 5), that means the first set's 3rd vertex is linked to the other
        set's 2nd and 5th vertices (all these indices are zero-based).
        :return: the number of bytes written to file
        """
        vertextargets_raw = []
        for otherset_vertidxs in vertextargets:
            vertextargets_raw.extend(otherset_vertidxs)
            vertextargets_raw.append(-1)
        num_rawvalues = len(vertextargets_raw)

        num_bytes = self._write_uint32(num_rawvalues)
        num_bytes += self._write_int32(*vertextargets_raw)
        return num_bytes

    def _write_vertices(self, vertices: Vertices) -> int:
        """write vertices to file

        :param vertices: a Vertices namedtuple
        :return: number of bytes written to file
        """
        # in an XG file, coordinates have an unknown (probably unused) 4th value
        coords_padded = ((x, y, z, 1.0) for x, y, z in vertices.coords)

        has_coords, has_normals, has_colors, has_texcoords = (bool(x) for x in vertices)

        vertices_interleaved_semiflat: List[Collection[float]] = []
        for (coord_padded, normal, color, texcoord) in zip_longest(
            coords_padded,
            vertices.normals,
            vertices.colors,
            vertices.texcoords,
            fillvalue=None,
        ):
            # add data pertaining to the current vertex
            for is_present, value in zip(
                (has_coords, has_normals, has_colors, has_texcoords),
                (coord_padded, normal, color, texcoord),
            ):
                if is_present:
                    vertices_interleaved_semiflat.append(value)

        # at this point, vertices_interleaved_semiflat looks like
        #  [ [coord1], [normal1], [color1], [texcoord1], [coord2), [normal2], ... ]
        #  but with any missing attributes omitted

        vertex_flags = (
            has_coords | (has_normals << 1) | (has_colors << 2) | (has_texcoords << 3)
        )
        num_bytes = self._write_uint32(vertex_flags)

        num_vertices = max(len(x) for x in vertices)
        num_bytes += self._write_uint32(num_vertices)

        vertices_interleaved_flat = chain.from_iterable(vertices_interleaved_semiflat)
        num_bytes += self._write_float32(*vertices_interleaved_flat)
        return num_bytes

    def _write_pstr(self, string) -> int:
        """write string to file as a Pascal string

        a Pascal string is 1 byte (length) followed by that many bytes. The string is
        encoded using Shift-JIS.

        :return: number of bytes written to file
        """
        bstr = string.encode(encoding="sjis")
        size = len(bstr)
        bsize = size.to_bytes(1, byteorder="little")
        self._file.write(bsize + bstr)
        return 1 + size

    def _write_uint32(self, *uints: int) -> int:
        """write uints to file as unsigned 32-bit integers (little-endian)

        :param uints: unsigned int or ints to write to file
        :return: number of bytes written to file
        """
        size = len(uints)
        fmt = f"<{size:d}I"
        uints_as_bytes = pack(fmt, *uints)
        self._file.write(uints_as_bytes)
        return len(uints_as_bytes)

    def _write_int32(self, *ints: int) -> int:
        """write uints to file as signed 32-bit integers (little-endian)

        :param ints: signed int or ints to write to file
        :return: number of bytes written to file
        """
        size = len(ints)
        fmt = f"<{size:d}i"
        ints_as_bytes = pack(fmt, *ints)
        self._file.write(ints_as_bytes)
        return len(ints_as_bytes)

    def _write_float32(self, *floats: float) -> int:
        """write floats to file as 32-bit floats (little-endian)

        :param floats: floating-point values to write to file
        :return: number of bytes written to file
        """
        size = len(floats)
        fmt = f"<{size:d}f"
        floats_as_bytes = pack(fmt, *floats)
        self._file.write(floats_as_bytes)
        return len(floats_as_bytes)

    def __del__(self):
        if self._autoclose:
            self._file.close()
