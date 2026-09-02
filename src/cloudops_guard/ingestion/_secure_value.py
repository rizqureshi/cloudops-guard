"""Internal base for value objects carrying at least one plaintext-
secret-shaped attribute (`token_format.ParsedToken.secret`,
`token_issuance.ProvisionedToken.token`) that must never be recoverable
through ordinary generic serialization -- `dataclasses.asdict`, `vars()`,
`json.dumps`, `pickle`, or an unredacted `repr`/`str`.

Deliberately **not** a `dataclasses.dataclass`, `typing.NamedTuple`, or any
other type with an automatic generic-serialization pathway: a frozen
dataclass's `repr`/`str` can be redacted, but `dataclasses.asdict()` walks
its `__dataclass_fields__` directly and reproduces every field's real
value regardless of `repr`; a `NamedTuple` is a tuple, and tuples
serialize their elements automatically via `json.dumps`, `pickle`, and
plain iteration. A plain class with `__slots__` and no `__dict__` has none
of these automatic pathways -- `dataclasses.asdict` and `dataclasses.
is_dataclass` are simply `False`/raise, `vars()` raises because there is
no `__dict__` to return, and `json.dumps` raises because the object is not
one of the types the encoder knows how to handle. Pickling is the one
generic-serialization pathway a slotted object *would* otherwise support
by default (the default `__reduce_ex__` protocol collects every `__slots__`
value into picklable state) -- `__reduce__`/`__getstate__` below refuse it
explicitly.

Not part of any public interface; imported only by `token_format.py` and
`token_issuance.py`.
"""

from __future__ import annotations

from typing import NoReturn


class ImmutableRedactedValue:
    """Subclasses must declare their own `__slots__` (this base
    contributes none, so a subclass's slots are the object's only
    attributes -- no instance `__dict__` exists anywhere in the MRO),
    set every attribute via `object.__setattr__` inside `__init__` (this
    class's own `__setattr__` refuses everything else, including from
    within a subclass's own methods), and define their own `__repr__`/
    `__str__` that redacts whichever attribute holds a secret.
    """

    __slots__ = ()

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable.")

    def __reduce__(self) -> NoReturn:
        # The primary pickle hook: refusing it here is sufficient on its
        # own to make `pickle.dumps`/`copy.deepcopy` fail closed, since
        # both consult `__reduce_ex__` (which falls back to this) before
        # any other state-collection path.
        raise TypeError(f"{type(self).__name__} is not picklable.")

    def __getstate__(self) -> NoReturn:
        # Belt-and-suspenders alongside __reduce__: some tooling
        # (certain copy/serialization libraries) consults __getstate__
        # directly rather than going through __reduce_ex__.
        raise TypeError(f"{type(self).__name__} does not support state extraction.")
