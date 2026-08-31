"""Page types as data, one module per type.

The building blocks live in ``pagetypes.core.*``, the registry and its accessors in
``pagetypes._registry``, and each page type in its own module beside this one. This init
module holds no logic and re-exports nothing; consumers import from the submodules
directly. The file stays because a regular package needs it and the HMR finder resolves
the package through it.
"""
