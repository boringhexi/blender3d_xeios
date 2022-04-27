"""xgscenewriter.py: write an xgscene.XgScene instance to file

For better documentation of XG file contents, see:
http://gitaroopals.shoutwiki.com/wiki/.XG
"""

from struct import pack
from typing import BinaryIO, List, NamedTuple, Optional, Tuple, Union

from .xgscene import XgNode, XgScene

DEBUG = False  # whether to print debug messages


def dbg(s):
    if DEBUG:
        print(s)


class XgSceneWriter:
    """an XgSceneWriter to write an XgScene to an XG file

    usage:
    xw = XgSceneWriter(fileobj) or xw = XgSceneWriter.from_path(filepath)
    xw.write_xgscene(my_xgscene)
    """

    def __init__(self, file: BinaryIO, autoclose: bool = False) -> None:
        """initialize an XgSceneWriter from a file object

        :param file: a binary file object. XG contents will be written to the current
        file position onward. File position after write_xgscene() will be at the end of
        the written contents
        :param autoclose: if True, automatically close the file when done parsing, if an
        error is encountered, or when this XgSceneWriter instance gets deleted
        """
        raise NotImplementedError(
            "Sorry, the code to initialize XgSceneWriter has not been written yet..."
        )

    @classmethod
    def from_path(cls, filepath: str, autoclose: bool = False) -> "XgSceneWriter":
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
        :return: the number of bytes written
        """
        raise NotImplementedError(
            "Sorry, the code to write XgScene to file has not been written yet..."
        )
