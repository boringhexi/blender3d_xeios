import os.path

from .xg.xgimporter import XgImporter


def load_xg(context, *, filepath, files, global_import_scale=None):
    dirname = os.path.dirname(filepath)
    for file in files:
        filepath = os.path.join(dirname, file.name)
        xgimporter = XgImporter.from_path(
            filepath, global_import_scale=global_import_scale
        )
        xgimporter.import_xgscene()
        del xgimporter
    context.view_layer.update()


def load_with_profiler(context, **keywords):
    import cProfile
    import pstats

    pro = cProfile.Profile()
    pro.runctx("load_xg(context, **keywords)", globals(), locals())
    st = pstats.Stats(pro)
    st.sort_stats("time")
    st.print_stats(0.1)
    st.print_callers(0.1)
    return {"FINISHED"}


def load(context, **keywords):
    # load_with_profiler(context, **keywords)
    load_xg(context, **keywords)
    return {"FINISHED"}
