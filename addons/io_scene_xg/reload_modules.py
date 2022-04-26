def reload_modules(*modules: str, pkg: str = __package__) -> None:
    """reload specified modules by name

    Intended way to use this from another script:
    1. Somehow check whether your imports have happened before (e.g. check existence of
        module that was imported last time, or a variable that was set last time)
    2. If so, import this function to your script, run this function on the names of
        modules that need to be reloaded.
        - Reload module B before reloading module A that imports module B. For circular
          imports, try reloading B then A then B?
    3. From now on, importing those modules elsewhere should give you the most
        up-to-date version.

    :param modules: iterable of str module names to reload
    :param pkg: str package name to use as anchor for resolving relative imports
        (e.g. pass the special variable __package__ from your script).
        Can pass None for absolute imports
    """
    from importlib import import_module, reload as reload_module

    for module in modules:
        reload_module(import_module(module, pkg))
