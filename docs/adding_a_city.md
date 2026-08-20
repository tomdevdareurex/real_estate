# Adding a City

Edit `config/cities.yaml` and add a city key with an English display name and category entries:

```yaml
cities:
  kaunas:
    display_name: Kaunas
    country: Lithuania
    categories:
      apartments:
        search_url: https://www.aruodas.lt/butai/kaune/
        listing_id_prefix: "1-"
        output_filename: apartments_kaunas.csv
      houses:
        search_url: https://www.aruodas.lt/namai/kaune/
        listing_id_prefix: "2-"
        output_filename: houses_kaunas.csv
```

No parser change is required. Search URLs are configuration data for a future retrieval layer; the
current pipeline reads listing HTML from local files.
