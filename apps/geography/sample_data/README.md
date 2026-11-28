# Sample geography

`prototype_villages.csv` holds the eight villages used in the prototypes, with the prototypes' coordinates and PIN codes. It is for local development and tests only. The coordinates are approximate and none of the rows has an LGD code.

Load it with:

```
python manage.py import_geography apps/geography/sample_data/prototype_villages.csv
```

Real master data for Andhra Pradesh should come from the official village lists, with LGD codes, in the same column layout. Run `import_geography --help` for the columns.
