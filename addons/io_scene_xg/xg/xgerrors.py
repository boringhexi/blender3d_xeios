"""xgerrors.py: Exceptions used by XG-handling scripts and modules"""


class XgException(Exception):
    """Base class for XG exceptions"""

    pass


class XgSceneError(XgException):
    """Error creating or using an XgScene"""

    pass


# read/parse errors
class XgReadError(XgException):
    """Error while reading/parsing an XgScene from an XG file

    offset: position in file at which the error occured.
        If specified, offset is prepended to the error message,
        e.g. offset 184: expected '{'
    """

    def __init__(self, message: str, offset: int = None) -> None:
        self.offset = offset
        self.mssg = message

    def __str__(self) -> str:
        if self.offset is None:
            return self.mssg
        else:
            return f"offset {self.offset}: {self.mssg}"


class XgInvalidFileError(XgReadError):
    """File being read is not a valid XG file"""

    pass


# import errors
class XgImportError(XgException):
    """Error while importing an XgScene into Blender"""

    pass


class ImageMissingError(XgImportError):  # TODO currently unused
    """One or more required image files could not be found"""

    pass
