# Schemas

## Field map (`field_map.schema.json`)

This is for a later step (Step F) and is **not wired up yet**: nothing in
va-stack calls it, and nothing is sent anywhere.

The idea: for a flat or scanned blank form, the app may, only after asking
you, give a bot the blank form and the **names** of the data points the
vault could fill, never values. The bot answers with a field map that says
where each data point goes, and which data points it could not place.

The schema (JSON Schema Draft 2020-12, local `$ref` only) has two shapes:

- **Request (out):** `schema_version`, `kind` = `field_map_request`, `form`
  (`id`, `name`, `sha256`, `page_count`, `page_sizes`) and `data_points` (a
  list of names).
- **Field map (in):** `schema_version`, `kind` = `field_map`, `form_sha256`,
  `fields` (each with `page`, `rect` in PDF points, `type` of text, checkbox
  or date, `data_point`, and an optional short `label` and `format`) and
  `missing` (a list of names).

Every object rejects unknown keys, and strings and lists have size caps.

`scripts/field_map.py` adds the rules a schema cannot hold: names only,
never values. Names must be on a fixed allowlist (`DATA_POINTS`), and form
ids, form names and labels are refused when they look like a value (digit
runs, `@`, dates or long free text). `build_request()` is the only way to
make a request, and it asks the vault for names only. `parse_field_map()`
checks a returned map against the request it answers: pages, boxes, field
types and data points.
