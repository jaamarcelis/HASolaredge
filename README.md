# SolarEdge (JMA) – custom integratie voor Home Assistant

Losse, installeerbare versie van de standaard Home Assistant
[SolarEdge-integratie](https://www.home-assistant.io/integrations/solaredge),
overgenomen uit
[`home-assistant/core` › `homeassistant/components/solaredge`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/solaredge)
(stand van release **2026.11.0**).

De integratie heeft een eigen domein, `solaredge_jma`, en heet in Home Assistant
**SolarEdge (JMA)**. Ze vervangt de ingebouwde SolarEdge-integratie dus niet en
kan er gewoon naast draaien.

### Verschillen met de core-integratie

- Eigen domein `solaredge_jma`. Bestaande configuraties van de ingebouwde
  SolarEdge-integratie worden niet overgenomen: voeg de site opnieuw toe onder
  *SolarEdge (JMA)*. Draai je beide voor dezelfde site, dan krijgen de entiteiten
  van deze integratie een achtervoegsel (bijv. `_2`) in hun entity-ID.
- Geen automatische ontdekking via DHCP; de ingebouwde integratie doet dat al.
- De client voor de SolarEdge Monitoring API zit in de integratie zelf
  ([`api.py`](custom_components/solaredge_jma/api.py)), gebaseerd op
  [aiosolaredge](https://github.com/bdraco/aiosolaredge) 1.0.2 (MIT-licentie).
  Het pip-pakket `aiosolaredge` is daardoor niet meer nodig.
- Inloggen met gebruikersnaam en wachtwoord van het webportaal (pakket
  `solaredge-web`, productiestatistieken per paneel) is verwijderd. De
  integratie werkt alleen met een API-sleutel en heeft geen externe
  Python-pakketten nodig.

## Vereisten

- Home Assistant **2026.11.0** of nieuwer (de code gebruikt API's en
  Python 3.14-syntax uit die versie).

## Installatie

### Via HACS (custom repository)

1. HACS → menu rechtsboven → *Custom repositories*.
2. Voeg de URL van deze repository toe met type **Integration**.
3. Installeer **SolarEdge (JMA)** en herstart Home Assistant.

### Handmatig

1. Kopieer de map `custom_components/solaredge_jma` naar
   `<config>/custom_components/solaredge_jma`.
2. Herstart Home Assistant.

Daarna: *Instellingen → Apparaten & diensten → Integratie toevoegen →
SolarEdge (JMA)*.

## Configuratie

Geef het site-ID en de API-sleutel op. De API-sleutel maak je aan in het
SolarEdge-monitoringportaal. Daarmee krijg je de sensoren van de site
(vermogen, energie, opslag, power flow, enz.).

## Bijwerken vanuit Home Assistant core

1. Kopieer de bestanden uit `homeassistant/components/solaredge` van de gewenste
   release-tag naar `custom_components/solaredge_jma`, behalve `strings.json`.
2. Pas daarna dezelfde wijzigingen opnieuw toe:
   - `DOMAIN = "solaredge_jma"` in `const.py`; in `manifest.json` `domain`
     `solaredge_jma`, `name` `SolarEdge (JMA)` en geen `dhcp`.
   - `from aiosolaredge import SolarEdge` vervangen door
     `from .api import SolarEdge`.
   - De web-login weghalen: `SolarEdgeModulesCoordinator`, de `web_auth`- en
     `api_auth`-secties in de config flow (API-sleutel verplicht maken),
     `aiosolaredge`, `solaredge-web` en de `recorder`-afhankelijkheid.
3. Zet `strings.json` om naar `translations/en.json`. Vervang daarbij elke
   `[%key:...%]`-verwijzing door de tekst zelf (zie `homeassistant/strings.json`
   in core), want custom integraties lossen die verwijzingen niet op.
4. Zet `version` in `manifest.json` en `homeassistant` in `hacs.json` op de nieuwe
   release.

## Licentie

Apache License 2.0. Zie [LICENSE.md](LICENSE.md). De oorspronkelijke code komt
van de Home Assistant-auteurs.
