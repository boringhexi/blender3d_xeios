bl_info = {
    "name": "Gitaroo Man",
    "author": "boringhexi",
    "version": (0, 1, 3),
    "blender": (3, 1, 0),
    "location": "File > Import-Export",
    "description": "Import XG",  # , SSQ
    "warning": "",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, axis_conversion

# Make the entire addon reloadable by Blender:
# The "Reload Scripts" command reloads only this file (the top-level __init__.py).
# That means it won't reload our modules imported by this file (or other modules
# imported by those modules). So instead, the code below will reload our modules
# whenever this file is reloaded.
if "_this_file_was_already_loaded" in locals():  # detect the reload
    from .reload_modules import reload_modules

    # Order matters. Reload module B before reloading module A that imports module B
    modules_to_reload = (
        ".xg.xgerrors",
        ".xg.xganimsep",
        ".xg.xgscene",
        ".xg.xgscenereader",
        ".xg.xgimporter",
        ".xg",
        ".import_xg",
    )
    reload_modules(*modules_to_reload, pkg=__package__)
_this_file_was_already_loaded = True  # to detect the reload next time
# After this point, any imports of the modules above will be up-to-date.


GLOBAL_SCALE = 32  # power-of-two fraction will have less floating-point error
GLOBAL_IMPORT_SCALE = 1 / GLOBAL_SCALE
GLOBAL_EXPORT_SCALE = GLOBAL_SCALE


class ImportXG(bpy.types.Operator, ImportHelper):
    """Import an XG file"""

    bl_idname = "import_scene.xg"
    bl_label = "Import XG"
    bl_options = {"PRESET", "UNDO"}
    filename_ext = ".XG"

    filter_glob: StringProperty(default="*.XG", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.PropertyGroup)

    def execute(self, context):
        # to reduce Blender startup time, delay import until now
        from . import import_xg

        keywords = self.as_keywords(ignore=("filter_glob",))
        keywords["global_import_scale"] = GLOBAL_IMPORT_SCALE
        return import_xg.load(context, **keywords)

    def draw(self, context):
        pass


def menu_func_import(self, context):
    self.layout.operator(ImportXG.bl_idname, text="Gitaroo Man model (.XG)")


classes = (ImportXG,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in classes:
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
