import os.path

from .xg.xgexporter import XgExporter
from .xg.xgscenewriter import XgSceneWriter


def save_xg(
    context, *, filepath, use_selection=True, global_export_scale=None, **keywords
):
    xgwriter = XgSceneWriter.from_path(filepath=filepath, autoclose=True)
    xgexporter = XgExporter(
        global_export_scale=global_export_scale, use_selection=use_selection
    )
    xgscene = xgexporter.export_xgscene()
    del xgexporter
    xgwriter.write_xgscene(xgscene)


def save_with_profiler(context, **keywords):
    import cProfile
    import pstats

    pro = cProfile.Profile()
    pro.runctx("save_xg(context, **keywords)", globals(), locals())
    st = pstats.Stats(pro)
    st.sort_stats("time")
    st.print_stats(0.1)
    st.print_callers(0.1)
    return {"FINISHED"}


def save(context, **keywords):
    # save_with_profiler(context, **keywords)
    save_xg(context, **keywords)
    return {"FINISHED"}
