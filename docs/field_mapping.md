# Field Mapping

`config/field_mappings_lt_en.yaml` contains three sections:

- `fields`: Lithuanian labels to stable English model fields.
- `categories`: Lithuanian categorical values to normalized English values.
- `features`: displayed feature text to stable Boolean keys.

Unknown labels are never discarded. They remain in `raw_attributes_json` and are aggregated in
`data/processed/unknown_fields.csv`. Add a mapping only after confirming the label's meaning and
unit. Ambiguous area labels must remain raw rather than being assigned to the wrong area column.
