"""Running plugin source code as modules."""

import imp


def load_source(name, source):
    """Run a plugin's source code as a new module called name, and return the module.

    The module is not added to sys.modules, so two plugins may have the same name.
    """
    module = imp.new_module(name)
    module.__file__ = f"<plugin {name}>"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module
