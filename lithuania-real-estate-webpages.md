# Lithuanian Real Estate Webpages

This list focuses on webpages that are genuinely tied to the Lithuanian market. I filtered out generic international portals and kept pages that help with appraisal, market research, pricing trends, and investment analysis.

## Best for online appraisal / valuation

- [Ober-Haus valuation services](https://www.ober-haus.lt/en/services/valuation/) - Lithuania-focused professional property and business valuation service. Strongest match for formal appraisal.
- [KvartalAI property valuation](https://kvartalai.lt/vertinimas) - AI-based preliminary property valuation using Lithuanian transaction data.
- [Nepriklausomi real estate valuation](https://nepriklausomi.lt/en/real-estate-valuation/) - Lithuanian valuation service. I could not fully fetch the page in this session, but it appears to be a local valuation offering.

## Best for market research / local pricing intelligence

- [Aruodas market trends](https://m.en.aruodas.lt/tendencijos?district_id=1) - Local supply and average price trends for Vilnius, Kaunas, and Klaipėda, including microdistrict-level pricing.
- [KvartalAI market analytics](https://kvartalai.lt/rinkos-analitika) - Market statistics, price dynamics, and transaction history for Lithuania.
- [Ober-Haus Lithuania/Vilnius Real Estate Market Report 2026](https://www.ober-haus.lt/en/lithuania-vilnius-real-estate-market-report-2026/) - Annual market review covering investment transactions plus office, retail, warehouse, residential, and land markets.
- [Inreal Lithuania economic and real estate market review](https://www.inreal.lt/en/lithuanias-economy-is-strengthening-while-the-housing-market-is-picking-up-pace/) - Local market commentary and review with housing, commercial property, and macro context.

## Useful for investors / flipping / rent-vs-buy analysis

- [KvartalAI rent or buy calculator](https://kvartalai.lt/irankiai?sk=nuoma-pirkimas) - Useful for comparing long-term rent versus purchase economics.
- [KvartalAI mortgage calculator](https://kvartalai.lt/irankiai?sk=hipoteka) - Helpful for quick financing assumptions when sizing a flip or buy-to-let deal.
- [KvartalAI crowdfunding](https://kvartalai.lt/crowdfunding) - Lithuanian real estate investment projects, more relevant for market exposure and project investing than direct house flipping.
- [KvartalAI property search](https://kvartalai.lt/paieskos) - Local listings with market context, AI search, and 3D tours.

## Official sources for previous transaction prices

- [Registrų centras: property transaction price report](https://www.registrucentras.lt/paslauga/db88b814-55e1-40e5-bea8-6c41e6864f6d) - The official source for prices recorded in Lithuanian real estate transactions. Any person can order a report using criteria such as municipality, value zone, period, property type, use, construction year, and floor-area range. As of 2026-08-17, one query costs EUR 22.60 including VAT and returns up to 25 transactions in XLSX format.
- [Registrų centras real estate services](https://www.registrucentras.lt/paslaugos/nturtas) - Official directory containing transaction-price reports, transaction-count reports, registry extracts, historical assessed-value certificates, and public value searches.
- [REGIA map](https://regia.lt/lt/zemelapis/) - Use the `Verčių zonos` layer to identify the value zone required when ordering a Registrų centras transaction-price report.
- [Lithuanian Open Data Portal: Housing Price Index weights](https://data.gov.lt/datasets/2521/) - Official aggregate housing-market statistics based on Registrų centras transaction data. Useful for market direction, but it does not publish individual sale prices or address-level transaction histories.

### Which Lithuanian webpages contain historical data?

| Webpage | What historical data it provides | Actual completed prices? | Suitable for flip comparables? |
|---|---|---:|---:|
| [Registrų centras transaction-price report](https://www.registrucentras.lt/paslauga/db88b814-55e1-40e5-bea8-6c41e6864f6d) | Up to 25 transactions selected by value zone, period, property type, area, construction period, and other criteria | Yes, anonymized | **Yes - primary source** |
| [BūstoRadar trends](https://www.bustoradar.lt/tendencijos) | Price changes over 6, 12, and 24 months by city or district; the site says its analytics use Registrų centras data | Aggregate only | Useful for trend adjustment, not individual comps |
| [BūstoRadar price map](https://www.bustoradar.lt/zemelapis) | Average prices by district | Aggregate only | Useful for location screening |
| [KvartalAI market analytics](https://kvartalai.lt/rinkos-analitika) | The site claims price dynamics and transaction history based on Registrų centras data | Not independently verified | Treat as secondary until the displayed records and methodology can be checked |
| [NTsandoriai.lt](https://www.ntsandoriai.lt/) | Educational content explaining completed prices and market statistics | No searchable transaction database found | Background reading only |
| [UNTU valuation](https://www.untu.lt/) | Preliminary address-based value estimate after answering property questions | No transaction list shown | Automated reasonableness check only |
| [Aruodas trends](https://m.en.aruodas.lt/tendencijos?district_id=1) | Historical advertised supply prices and listing counts | No - asking prices | Useful for current competition, not proof of sale price |

**Bottom line:** Lithuania has webpages with historical market data, but I found no verified free public webpage exposing exact-address histories of completed sales. For actual comparable transaction rows, order the Registrų centras report. For historical direction, use BūstoRadar, Aruodas, and official aggregate indices.

### Important limitations

- The standard Registrų centras transaction-price report contains actual registered prices, but the properties are anonymized. Location is shown only to municipality and value-zone precision, not by exact address.
- Registrų centras public searches for `vidutinė rinkos vertė` or `mokestinė vertė` show mass-assessed values. These are not the same as the price paid in a previous sale.
- Lithuania does not appear to offer a free public address-by-address sold-price history comparable to the UK Land Registry sold-price search.
- For comparable-sales research, define a narrow value zone, property type, date range, construction period, and floor-area range when ordering the report.

## Recommended workflow for analysing an apartment flip

The goal is to estimate two different values:

1. **Current unrenovated market value** - what similar apartments in similar condition actually sell for.
2. **Conservative after-renovation value (ARV)** - what comparable renovated apartments actually sell for.

### 1. Define the comparable property

Record the target apartment's value zone, municipality, floor area, room count, construction period, floor, building type, condition, heating, lift, balcony, parking, and energy characteristics.

### 2. Order official completed-sale evidence

Use the Registrų centras transaction-price report. Request the narrowest practical selection:

- Same value zone
- Apartments only
- Previous 3 to 6 months, extending to 12 months if too few sales exist
- Similar floor-area interval
- Similar construction period
- Similar property use and building characteristics where the form permits

The standard report returns up to 25 actual registered transactions. Because addresses are anonymized, treat it as value-zone evidence rather than proof that a particular building sold at a particular price.

### 3. Build two comparable groups

- **Unrenovated group:** use dated or visibly poor-condition listings from Aruodas or KvartalAI and anchor their likely sale level to Registrų centras completed-sale data.
- **Renovated group:** use genuinely renovated competing listings and anchor them to the upper end of completed sales in the same value zone.

Do not mix asking prices and completed prices without labeling them. Aruodas and portal listings show seller expectations; Registrų centras shows registered transaction prices.

### 4. Calculate conservative values

Use median EUR/m² rather than the highest comparable:

```text
Estimated value = Adjusted median comparable EUR/m² x Apartment area
```

Apply explicit deductions for inferior floor, no lift, poor building condition, noise, weak energy performance, missing parking or balcony, legal/layout problems, and unusually high building costs.

For ARV, use the lower of:

- Adjusted renovated-comparable value
- A conservative discount to the best directly competing renovated listings

### 5. Run the acquisition test

Insert the conservative ARV into the full flip model in `Download the apartment-flipping research file.md`. Do not purchase based on the portal's asking-price average or Registrų centras mass-assessed value.

### Evidence hierarchy

1. Registrų centras completed transaction prices
2. Very similar recent properties with known final prices from brokers, valuers, or transaction participants
3. Current competing listings on Aruodas and KvartalAI
4. District and city price trends
5. Automated valuation and mass-assessed value as reasonableness checks only

## Notes

- I did not find a clearly dedicated Lithuanian .lt cap-rate calculator in this pass.
- For cap rate work, the most practical approach is to combine local purchase price data, rent data, and vacancy/expense assumptions from the pages above, then calculate cap rate manually.
- If you want, the next useful step is to turn this into a more investor-oriented shortlist with columns like: valuation, market trends, rent data, transaction data, and suitability for flipping.
