"""xganimsep.py: parse and write additional XG animation separation data

Animation separation data is stored before the XG model's data in the XGM container.
For better documentation, see the Animation Entry section in:
http://gitaroopals.shoutwiki.com/wiki/.XGM
"""

from typing import NamedTuple, List, BinaryIO, Sequence, Optional, AnyStr, Union
from struct import Struct

_struct_uint32 = Struct("<I")
_struct_animsep_entry = Struct("<4f4I")
_entrysize = _struct_animsep_entry.size


class Constants:
    class SpeedMode:
        TEMPO = 0
        REGULAR = 1


class AnimSepEntry(NamedTuple):
    playback_length: float = 1
    keyframe_interval: float = 1
    unused60: float = 60
    start_keyframe_idx: float = 0
    speed_mode: int = Constants.SpeedMode.TEMPO
    unused01: int = 0
    unused02: int = 0
    unused03: int = 0


def read_animseps(
    file: Union[BinaryIO, AnyStr], num_entries: Optional[int] = None
) -> List[AnimSepEntry]:
    """read and return num_entries animsep entries from file

    :param file: file path, or a file object in 'rb' mode. If a file object,
        file position needs to be at the start of the animsep data, and file position
        will be at the end of the animsep data after returning.
    :param num_entries: if None, will read entries until end of the file (intended
        for standalone .animsep files). If num_entries is provided, will only read that
        many entries (intended for reading from within an XGM container).
    :return: list of AnimSepEntry namedtuples
    """
    do_close = False
    if not hasattr(file, "read"):
        file = open(file, "rb")
        do_close = True
    try:
        if num_entries is None:
            animsepdata = file.read()
            num_entries = len(animsepdata) // _entrysize
        else:
            animsepdata = file.read(_entrysize * num_entries)

        ret = []
        for i in range(num_entries):
            entrydata = animsepdata[i * _entrysize : i * _entrysize + _entrysize]
            entry = AnimSepEntry._make(_struct_animsep_entry.unpack(entrydata))
            ret.append(entry)
        return ret
    finally:
        if do_close:
            file.close()


def write_animseps(file: BinaryIO, animsep: Sequence[AnimSepEntry]) -> None:
    """write animsep entries to file

    :param file: file object in 'wb' mode. animsep data will be written starting from
        the current file position. After returning, file position will be at the end of
        the written animsep data.
    :param animsep: sequence of AnimSepEntry namedtuples to be written to file
    """
    for entry in animsep:
        file.write(_struct_animsep_entry.pack(*entry))
