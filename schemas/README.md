# Schemas

## Field map (`field_map.schema.json`)

This is for a later step (Step F) and is **not wired up yet**: nothing in
va-stack calls it, and nothing is sent anywhere.

The idea: for a flat or scanned blank form, the app may, only after asking
you, give a bot the blank form and the **names** of the data points your
vault holds, never values. The bot answers with a field map that says where
each data point goes on the page, and which data points the form asks for
that you have not entered yet. The program then fills in the values on your
computer.

The schema (JSON Schema Draft 2020-12, local `$ref` only) has two shapes:

- **Request (out):** `schema_version`, `kind` = `field_map_request`, `form`
  (`id`, `name`, `sha256`, `page_count`, `page_sizes`) and `data_points` (a
  list of names).
- **Field map (in):** `schema_version`, `kind` = `field_map`, `form_sha256`,
  `fields` (each with `page`, `rect` in PDF points, `type` of text, checkbox
  or date, `data_point`, and an optional short `label`, `format`, `comb` and
  `max_length`) and `missing` (a list of names).

Page sizes and boxes are for the page as you see it: a page turned by
`/Rotate` reports its turned size, and `to_pdf_rect()` maps a box back to
the page's own coordinates. Every object rejects unknown keys, and strings
and lists have size caps.

## Data points (`data_points.json`)

The one list of data-point names. Each entry has a `name` (lowercase
snake_case), a `category` and a `type`. The categories are `identity`,
`contact`, `service`, `banking` and `claim`; they will order the tabs of
the master data window. The types are `text`, `date`, `checkbox` and
`choice` (one of a fixed set, written out or ticked).

Identifier names such as `ssn`, `date_of_birth` and `mailing_address` are
in the list because real VA forms ask for them. Only the names can leave
your computer, never values. Which values go into a draft is still decided
by `scripts/form_fill.py` and `scripts/identifier_blocklist.py`.

The schema repeats the names as an enum. After editing the list, run
`python tools/sync_field_map_enum.py`; `--check` reports drift, and the
tests and `scripts/field_map.py` refuse a schema that has drifted.

`scripts/field_map.py` adds the rules a schema cannot hold: names only,
never values. Names must be in the list, and form ids, form names and
labels are refused when they look like a value (an identifier, a date or
long free text). `build_request()` is the only way to make a request, and
it asks the vault for names only. `parse_field_map()` checks a returned map
against the request it answers: pages, boxes, field types and data points.
Error messages point at the place (such as `$.fields[2].label`), never at
the text.
