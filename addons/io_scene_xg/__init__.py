bl_info = {
    "name": "Xeios XG format",
    "author": "boringhexi",
    "version": (0, 1, 3),
    "blender": (3, 1, 0),
    "location": "File > Import-Export",
    "description": "For Xeios engine games like Gitaroo Man and まげる つける はしーる",
    "warning": "",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

# Make the entire addon reloadable by Blender:
# The "Reload Scripts" command reloads only this file (the top-level __init__.py).
# That means it won't reload our modules imported by this file (or other modules
# imported by those modules). So instead, the code below will reload our modules
# whenever this file is reloaded.
if "_this_file_was_already_loaded" in locals():
    from .reload_modules import reload_modules

    # Order matters. Reload module B before reloading module A that imports module B
    modules_to_reload = (
        ".xg.xgerrors",
        ".xg.xganimsep",
        ".xg.xgscene",
        ".xg.xgscenereader",
        ".xg.xgscenewriter",
        ".xg.xgimporter",
        ".xg.xgexporter",
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


class ExportXG(bpy.types.Operator, ExportHelper):
    """Export to an XG file"""

    bl_idname = "export_scene.xg"
    bl_label = "Export XG"
    bl_options = {"PRESET"}

    filename_ext = ".XG"
    filter_glob: StringProperty(default="*.XG", options={"HIDDEN"})

    use_selection: BoolProperty(
        name="Selection Only",
        description="Export selected objects only",
        default=False,
    )

    def execute(self, context):
        # to reduce Blender startup time, delay import until now
        from . import export_xg

        keywords = self.as_keywords(ignore=("filter_glob",))
        keywords["global_export_scale"] = GLOBAL_EXPORT_SCALE
        return export_xg.save(context, **keywords)

    def draw(self, context):
        pass


class XG_PT_export_include(bpy.types.Panel):
    bl_space_type = "FILE_BROWSER"
    bl_region_type = "TOOL_PROPS"
    bl_label = "Include"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        sfile = context.space_data
        operator = sfile.active_operator

        return operator.bl_idname == "EXPORT_SCENE_OT_xg"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        sfile = context.space_data
        operator = sfile.active_operator

        layout.prop(operator, "use_selection")


def menu_func_import(self, context):
    self.layout.operator(ImportXG.bl_idname, text="Xeios (.XG)")


def menu_func_export(self, context):
    self.layout.operator(ExportXG.bl_idname, text="Xeios (.XG)")


classes = (ImportXG, ExportXG, XG_PT_export_include)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

    for cls in classes:
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
