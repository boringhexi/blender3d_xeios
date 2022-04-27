"""xgexporter.py: export Blender objects to XgScene"""

from mathutils import Euler, Matrix, Quaternion, Vector

from .xganimsep import AnimSepEntry, write_animseps
from .xgscene import Constants, XgNode, XgScene
from .xgscenewriter import XgSceneWriter


class XgExporter:
    """exports objects out of Blender as an XgScene"""

    def __init__(self, global_export_scale=None, use_selection=True):
        """create an XgExporter to export Blender objects as an XgScene instance

        :param global_export_scale: value by which to scale exported meshes and armature
        :param use_selection: if True, export only selected objects from Blender
        """
        self.warnings = []
        if global_export_scale is None:
            global_export_scale = 1
        self._global_import_scale = global_export_scale
        ges = global_export_scale
        self._global_export_mtx = Matrix(
            (
                [-ges, 0.00, 0.0, 0.0],
                [0.00, 0.00, -ges, 0.0],
                [0.00, ges, 0.0, 0.0],
                [0.00, 0.00, 0.0, 1.0],
            )
        )
        # matrix effect: scales -1.0 across X, rotates -90deg about X, scales by ges.
        #   In other words, it swaps from Blender's coordinate system to XG's (and
        #   scales as desired).

    def export_xgscene(self) -> XgScene:
        raise NotImplementedError(
            "Sorry, code to export XgScene from Blender has not been written yet..."
        )
