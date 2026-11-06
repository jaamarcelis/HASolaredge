# SolarEdge (JMA) – custom integratie voor Home Assistant

Custom integratie voor de **SolarEdge Monitoring API v2** met inloggen via
**OAuth 2.0** (SolarEdge Connect). De basis is de standaard Home Assistant
[SolarEdge-integratie](https://github.com/home-assistant/core/tree/dev/homeassistant/components/solaredge)
(release 2026.11.0). Die is herschreven voor v2, omdat SolarEdge de v1 API
(met API-sleutel) op **3 november 2026** heeft uitgeschakeld.

De integratie heeft een eigen domein, `solaredge_jma`, en heet in Home Assistant
**SolarEdge (JMA)**. Ze kan dus naast de ingebouwde SolarEdge-integratie draaien.

## Vereisten

- Home Assistant **2026.11.0** of nieuwer.
- Een (gratis) account op het
  [SolarEdge developer platform](https://developer.solaredge.com).
- Een ingestelde URL van Home Assistant (*Instellingen → Systeem → Netwerk*):
  de externe URL, of de interne als er geen externe is. De OAuth-redirect gaat
  naar de lokale callback op dat adres, dus je browser moet het kunnen bereiken.
  My Home Assistant wordt niet gebruikt.

## Installatie

### Via HACS (custom repository)

1. HACS → menu rechtsboven → *Custom repositories*.
2. Voeg de URL van deze repository toe met type **Integration**.
3. Installeer **SolarEdge (JMA)** en herstart Home Assistant.

### Handmatig

Kopieer `custom_components/solaredge_jma` naar
`<config>/custom_components/solaredge_jma` en herstart Home Assistant.

## Configuratie

### 1. Applicatie aanmaken bij SolarEdge

1. Log in op [developer.solaredge.com](https://developer.solaredge.com) met je
   SolarEdge-account (hetzelfde als voor mySolarEdge). Vul bij de eerste keer het
   developer-profiel in. Bij *Company name* mag je eigen naam staan, en het
   **Free**-tier volstaat.
2. Kies **Create Application** → service *Monitoring API* → **Site Access**.
3. Vul in:
   - **Redirect URI:** `<HA-URL>/api/solaredge_jma/oauth/callback`, bijvoorbeeld
     `https://ha.example.com/api/solaredge_jma/oauth/callback`. Hier hoort de URL uit de
     vereisten hierboven; het dialoog *Application credentials* in Home Assistant
     toont de exacte waarde. Wijzig je later de URL van Home Assistant, pas dan
     ook de redirect-URI bij SolarEdge aan.
   - **Origin URL:** de externe URL van je Home Assistant (of laat leeg als dat
     mag).
   - **Scopes:** `SITE_DATA` en `DEVICE_DATA`.
4. Kopieer de **Client ID** en het **Client Secret**. Het secret wordt maar één
   keer getoond.

### 2. Integratie toevoegen

1. *Instellingen → Apparaten & diensten → Integratie toevoegen →
   SolarEdge (JMA)*.
2. Vul het **site-ID** in (te zien in het monitoringportaal en de mySolarEdge-app).
3. Voer bij de vraag om *application credentials* de Client ID en het Client
   Secret in.
4. Je wordt naar SolarEdge gestuurd. Log in met je **mySolarEdge-account**, kies
   **dezelfde site** en klik op *Approve*.

De toestemming geldt 24 maanden. Daarna, of als je de toegang intrekt, vraagt Home
Assistant je via een melding om opnieuw in te loggen.

### Overstappen vanaf een v1-configuratie (API-sleutel)

Een bestaande configuratie van deze integratie met een API-sleutel start niet
meer. Home Assistant toont dan de melding dat opnieuw inloggen nodig is. Voeg
eerst de *application credentials* toe (stap 1), en doorloop dan via die melding
de OAuth-stappen. Het site-ID en de entiteiten blijven behouden. De API-sleutel
wordt uit de configuratie verwijderd.

## Credits en update-interval

Elke geslaagde API-aanroep kost **1 credit**. Het Free-tier geeft **2.000 credits
per maand** voor je hele developer-account, en maximaal 10 aanroepen per minuut.
Daarom haalt de integratie per aanroep zoveel mogelijk op:

| Gegevens | Endpoint | Frequentie |
|---|---|---|
| Energie vandaag / deze maand / dit jaar | `/sites/{id}/energy` (DAY) | elke cyclus |
| Productie, verbruik, import, export (energie en vermogen) | `/sites/{id}/meters/telemetry` | elke cyclus |
| Batterijen | `/sites/{id}/storage/telemetry` | elke cyclus, alleen als er batterijen zijn |
| Totale opbrengst (lifetime) | `/sites/{id}/lifetime-energy` | hooguit elke 6 uur |
| Sitegegevens en apparaten | `/sites/{id}`, `/sites/{id}/devices` | elke 24 uur |

Het interval wordt berekend uit het **maandbudget** (standaard 2.000), met 10%
marge. Met het Free-tier is dat ongeveer:

- **54 minuten** zonder batterij;
- **80 minuten** met batterij.

Pas het aan via **Configureren** bij de integratie:

- **Maandelijks creditbudget:** bijvoorbeeld 20.000 voor Business Starter.
- **Update-interval:** een vast interval in minuten, minimaal 15; `0` berekent
  het uit het budget.
- **Advanced power flow:** live vermogens via `/sites/{id}/power-flow`. Dit vereist
  het **Business Pro**- of Enterprise-tier en kost ongeveer 8.640 credits per
  maand. Staat deze optie uit, dan komen de vermogenssensoren (zon, net, verbruik,
  batterij) uit het laatste kwartier van de telemetrie.
- **Alleen overdag updaten:** tussen zonsondergang en zonsopgang worden geen
  API-aanroepen gedaan. Vink je dit aan, dan kies je in een volgende stap twee
  entiteiten met de tijd van **zonsopgang** en **zonsondergang**: een
  timestamp-sensor (bijvoorbeeld `sensor.sun_next_rising` en
  `sensor.sun_next_setting` van de Sun-integratie) of een datum/tijd-helper
  (`input_datetime`). Alleen het tijdstip van de dag telt,
  dus "volgende zonsopgang" werkt ook na zonsopgang.

### Alleen overdag updaten

- Na zonsondergang volgt nog **één laatste update**, zodat de dagtotalen compleet
  zijn. Daarna blijven de sensoren tot zonsopgang op hun laatste waarde staan.
- De bespaarde nachtcredits worden overdag gebruikt: het interval wordt berekend
  over de daglichturen plus 1 uur marge. Bij 9 uur daglicht en het Free-tier met
  batterij wordt dat **33 minuten** in plaats van 80. Het interval schuift
  automatisch mee met de seizoenen.
- Ook Advanced power flow peilt dan alleen overdag.
- Is een van de entiteiten onbekend of niet beschikbaar, dan wordt gewoon de hele
  dag geüpdatet (met het interval voor 24 uur).
- *Energie vandaag* toont na middernacht tot zonsopgang nog de waarde van
  gisteren; bij de eerste update na zonsopgang wordt dat bijgewerkt.

Is de maandlimiet bereikt, of is power flow niet beschikbaar voor je tier, dan
verschijnt er een melding onder *Reparaties*.

## Verschillen met de core-integratie

- SolarEdge Monitoring API v2 met OAuth 2.0 in plaats van v1 met API-sleutel.
  De API-client zit in de integratie zelf ([`api.py`](custom_components/solaredge_jma/api.py));
  er zijn geen externe Python-pakketten nodig.
- Eigen domein `solaredge_jma`, zonder DHCP-ontdekking.
- Geen inloggen met gebruikersnaam/wachtwoord (productiestatistieken per paneel).
- Een ander interval, afgestemd op het creditbudget.
- De vermogenssensoren komen uit 15-minutentelemetrie, tenzij Advanced power flow
  aan staat.
- De sensor *Sensors* (aantal sensoren) vervalt; v2 kent dat apparaattype niet.

## Bekende beperking: response-formaten

SolarEdge publiceert voor v2 geen OpenAPI-specificatie. De parsing van de
telemetrie, de apparatenlijst en power flow is gebaseerd op de
[documentatie](https://api-docs.solaredge.com/docs/developer-platform/mxj41549n0nxk-migrating-from-v1-to-v2)
en zoekt waarden op metrieknaam (`productionPower`, `stateOfEnergy`, …), ongeacht
de nesting. Kloppen er waarden niet, download dan de **diagnostics** van de
integratie. Daarin staan de (geanonimiseerde) ruwe API-responses, zodat de
parsing kan worden aangepast.

## Ontwikkeling en tests

Home Assistant draait niet op Windows. Draai de tests op Linux, WSL of in Docker:

```bash
pip install -r requirements_test.txt
pytest
```

## Bijwerken vanuit Home Assistant core

Deze integratie wijkt inmiddels sterk af van de core-integratie, die nog v1
gebruikte. Neem wijzigingen uit core alleen nog handmatig over.

## Licentie

Apache License 2.0. Zie [LICENSE.md](LICENSE.md). De oorspronkelijke code komt
van de Home Assistant-auteurs.
