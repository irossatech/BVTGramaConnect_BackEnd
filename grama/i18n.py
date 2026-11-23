"""The Telugu message catalogue: find, update, build and check it, without GNU gettext.

Every user-facing message is written in English inside _() (gettext or gettext_lazy). Its Telugu
lives in locale/te/LC_MESSAGES/django.po; Django reads the compiled django.mo next to it and answers
in the language of the request's Accept-Language (Telugu when it is missing). Both files are
committed, so a server needs nothing extra.

    python manage.py translations            add new messages to the .po, drop removed ones, build the .mo
    python manage.py translations --check    fail when a message has no Telugu or the .mo is out of date

Messages are found by reading the Python source (ast), not by running xgettext, so this works on
Windows laptops too. Django's own makemessages and compilemessages still work where gettext is
installed. A test (grama/tests/test_i18n.py) runs the check on every pull request.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

LANGUAGE = "te"
SOURCE_DIRS = ("apps", "grama")
SKIP_PARTS = {"tests", "migrations", "__pycache__"}
#: The Django admin is for developers' laptops only (off on every server), so it stays English.
SKIP_FILES = {"admin.py"}

#: Translation functions whose first argument is a message.
SINGULAR = {"gettext", "gettext_lazy", "gettext_noop"}
#: Plural and context forms: not used yet. Finding one fails loudly, so no message is silently missed.
UNSUPPORTED = {"ngettext", "ngettext_lazy", "pgettext", "pgettext_lazy", "npgettext", "npgettext_lazy"}

HEADER = {
    "Project-Id-Version": "BVT Grama Connect",
    "Language": LANGUAGE,
    "MIME-Version": "1.0",
    "Content-Type": "text/plain; charset=UTF-8",
    "Content-Transfer-Encoding": "8bit",
    "Plural-Forms": "nplurals=2; plural=(n != 1);",
}

#: Messages of Django REST Framework that reach users (field validation, request errors). DRF ships
#: no Telugu, so their Telugu is kept here with ours. The text must match DRF's exactly.
LIBRARY_MESSAGES = (
    # Fields
    "This field is required.",
    "This field may not be null.",
    "This field may not be blank.",
    "Not a valid string.",
    "Must be a valid boolean.",
    "Ensure this field has no more than {max_length} characters.",
    "Ensure this field has at least {min_length} characters.",
    "Enter a valid email address.",
    "This value does not match the required pattern.",
    "Enter a valid URL.",
    "Must be a valid UUID.",
    "A valid integer is required.",
    "A valid number is required.",
    "Ensure this value is less than or equal to {max_value}.",
    "Ensure this value is greater than or equal to {min_value}.",
    "String value too large.",
    "Ensure that there are no more than {max_digits} digits in total.",
    "Ensure that there are no more than {max_decimal_places} decimal places.",
    "Ensure that there are no more than {max_whole_digits} digits before the decimal point.",
    "Datetime has wrong format. Use one of these formats instead: {format}.",
    "Date has wrong format. Use one of these formats instead: {format}.",
    "Expected a date but got a datetime.",
    '"{input}" is not a valid choice.',
    'Expected a list of items but got type "{input_type}".',
    "This selection may not be empty.",
    "This list may not be empty.",
    "Ensure this field has at least {min_length} elements.",
    "Ensure this field has no more than {max_length} elements.",
    'Expected a dictionary of items but got type "{input_type}".',
    "This dictionary may not be empty.",
    "No file was submitted.",
    "The submitted data was not a file. Check the encoding type on the form.",
    "No filename could be determined.",
    "The submitted file is empty.",
    "Ensure this filename has at most {max_length} characters (it has {length}).",
    "Upload a valid image. The file you uploaded was either not an image or a corrupted image.",
    "Invalid data. Expected a dictionary, but got {datatype}.",
    "This field must be unique.",
    "The fields {field_names} must make a unique set.",
    'Invalid pk "{pk_value}" - object does not exist.',
    "Incorrect type. Expected pk value, received {data_type}.",
    # Requests
    "A server error occurred.",
    "Invalid input.",
    "Malformed request.",
    "Incorrect authentication credentials.",
    "Authentication credentials were not provided.",
    "You do not have permission to perform this action.",
    "Not found.",
    'Method "{method}" not allowed.',
    "Could not satisfy the request Accept header.",
    'Unsupported media type "{media_type}" in request.',
    "Request was throttled.",
    "Expected available in {wait} second.",
    "Expected available in {wait} seconds.",
    "Invalid page.",
    "Invalid cursor",
    # Django's password validators (AUTH_PASSWORD_VALIDATORS); Django's own Telugu has none of them.
    "The password is too similar to the %(verbose_name)s.",
    "Your password can’t be too similar to your other personal information.",
    "This password is too common.",
    "Your password can’t be a commonly used password.",
    "This password is entirely numeric.",
    "Your password can’t be entirely numeric.",
)

#: Library messages with a plural form (ngettext): (singular, plural), exactly as the library has them.
LIBRARY_PLURALS = (
    (
        "This password is too short. It must contain at least %d character.",
        "This password is too short. It must contain at least %d characters.",
    ),
    (
        "Your password must contain at least %(min_length)d character.",
        "Your password must contain at least %(min_length)d characters.",
    ),
)

#: Placeholders a translation must keep: %(name)s, %s, %d and {name} (DRF's str.format fields).
PLACEHOLDER = re.compile(r"%\(\w+\)[sdif]|%[sdif]|\{\w*\}")


def base_dir() -> Path:
    return Path(settings.BASE_DIR)


def po_path() -> Path:
    return base_dir() / "locale" / LANGUAGE / "LC_MESSAGES" / "django.po"


def mo_path() -> Path:
    return po_path().with_suffix(".mo")


@dataclass
class Extracted:
    #: msgid -> places it is used ("apps/posts/media.py:206"), in the order found.
    messages: dict[str, list[str]] = field(default_factory=dict)
    #: Calls that cannot be extracted: a plural/context form, or a message that is not a literal.
    problems: list[str] = field(default_factory=list)
    #: msgid -> msgid_plural, for the messages that have a plural form.
    plurals: dict[str, str] = field(default_factory=dict)

    def add(self, msgid: str, where: str) -> None:
        self.messages.setdefault(msgid, []).append(where)


def _translation_names(tree: ast.Module) -> dict[str, str]:
    """Local name -> django.utils.translation function, from the module's imports."""
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "django.utils.translation":
            for alias in node.names:
                names[alias.asname or alias.name] = alias.name
    return names


def source_files(root: Path | None = None):
    root = root or base_dir()
    for folder in SOURCE_DIRS:
        for path in sorted((root / folder).rglob("*.py")):
            if path.name not in SKIP_FILES and not SKIP_PARTS.intersection(path.relative_to(root).parts):
                yield path


def extract(root: Path | None = None) -> Extracted:
    """Every message in _() / gettext() / gettext_lazy() / gettext_noop() in apps/ and grama/."""
    root = root or base_dir()
    found = Extracted()
    for path in source_files(root):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        names = _translation_names(tree)
        if not names:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            function = names.get(node.func.id)
            where = f"{rel}:{node.lineno}"
            if function in UNSUPPORTED:
                found.problems.append(f"{where}: {function}() is not supported by grama/i18n.py yet")
            elif function in SINGULAR:
                first = node.args[0] if node.args else None
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value, where)
                else:
                    found.problems.append(f"{where}: {function}() needs a literal string to be translated")
    for msgid in LIBRARY_MESSAGES:
        found.add(msgid, "grama/i18n.py (library message)")
    for msgid, plural in LIBRARY_PLURALS:
        found.add(msgid, "grama/i18n.py (library message)")
        found.plurals[msgid] = plural
    return found


def placeholders(text: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(text))


def _flags(msgid: str) -> list[str]:
    if re.search(r"%\(\w+\)[sdif]|%[sdif]", msgid):
        return ["python-format"]
    if re.search(r"\{\w+\}", msgid):
        return ["python-brace-format"]
    return []


def update(extracted: Extracted | None = None):
    """Bring the .po in line with the code. Returns (added, removed) msgids. Translations are kept."""
    import polib

    extracted = extracted or extract()
    path = po_path()
    catalogue = polib.pofile(str(path), wrapwidth=0) if path.exists() else polib.POFile(wrapwidth=0)
    catalogue.metadata = {**HEADER, **{k: v for k, v in catalogue.metadata.items() if k not in HEADER}}
    by_id = {entry.msgid: entry for entry in catalogue}
    added, removed = [], []
    for msgid, places in extracted.messages.items():
        entry = by_id.get(msgid)
        plural = extracted.plurals.get(msgid)
        if entry is None:
            if plural:
                entry = polib.POEntry(msgid=msgid, msgid_plural=plural, msgstr_plural={0: "", 1: ""})
            else:
                entry = polib.POEntry(msgid=msgid, msgstr="")
            catalogue.append(entry)
            added.append(msgid)
        entry.obsolete = False
        entry.occurrences = [(place, "") for place in places]
        entry.flags = sorted(set(_flags(msgid)) | ({"fuzzy"} if "fuzzy" in entry.flags else set()))
    for entry in list(catalogue):
        if entry.msgid and entry.msgid not in extracted.messages:
            if not entry.obsolete:
                removed.append(entry.msgid)
            if any(_texts(entry)):
                # Kept as an obsolete (#~) entry, so a message that comes back keeps its Telugu.
                entry.obsolete = True
            else:
                catalogue.remove(entry)
    # Code order, like makemessages: entries in the order their first use appears.
    order = {msgid: index for index, msgid in enumerate(extracted.messages)}
    catalogue.sort(key=lambda entry: (entry.obsolete, order.get(entry.msgid, len(order)), entry.msgid))
    path.parent.mkdir(parents=True, exist_ok=True)
    catalogue.save(str(path))
    return added, removed


def compile_mo() -> None:
    import polib

    polib.pofile(str(po_path()), wrapwidth=0).save_as_mofile(str(mo_path()))


def _texts(entry) -> list[str]:
    """The Telugu of an entry: one text, or one per plural form."""
    if entry.msgid_plural:
        return [entry.msgstr_plural[form] for form in sorted(entry.msgstr_plural)]
    return [entry.msgstr]


def _translated(catalogue) -> dict[str, tuple]:
    return {
        entry.msgid: tuple(_texts(entry))
        for entry in catalogue
        if entry.msgid and all(_texts(entry)) and not entry.obsolete and "fuzzy" not in entry.flags
    }


def check(extracted: Extracted | None = None) -> list[str]:
    """Problems that would show a user English, crash a message, or leave the .mo stale. [] is good."""
    import polib

    extracted = extracted or extract()
    problems = list(extracted.problems)
    if not po_path().exists():
        return [*problems, f"{po_path()} is missing: run python manage.py translations"]
    catalogue = polib.pofile(str(po_path()), wrapwidth=0)
    entries = {entry.msgid: entry for entry in catalogue if not entry.obsolete}
    for msgid, places in extracted.messages.items():
        entry = entries.get(msgid)
        if entry is None:
            problems.append(f"not in the .po (run python manage.py translations): {msgid!r} ({places[0]})")
        elif not all(_texts(entry)):
            problems.append(f"no Telugu: {msgid!r} ({places[0]})")
        elif "fuzzy" in entry.flags:
            problems.append(f"Telugu marked fuzzy (check it, then remove the flag): {msgid!r}")
        else:
            for text in _texts(entry):
                if placeholders(text) != placeholders(msgid):
                    problems.append(
                        f"placeholders differ: {msgid!r} has {placeholders(msgid)}, the Telugu has {placeholders(text)}"
                    )
    for msgid in entries:
        if msgid and msgid not in extracted.messages:
            problems.append(f"no longer used (run python manage.py translations): {msgid!r}")
    if not mo_path().exists():
        problems.append(f"{mo_path()} is missing: run python manage.py translations")
    elif _translated(polib.mofile(str(mo_path()))) != _translated(catalogue):
        problems.append("django.mo is out of date with django.po: run python manage.py translations")
    return problems
