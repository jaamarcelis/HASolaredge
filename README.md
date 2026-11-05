# SolarEdge – custom integratie voor Home Assistant

Losse, installeerbare versie van de standaard Home Assistant
[SolarEdge-integratie](https://www.home-assistant.io/integrations/solaredge),
overgenomen uit
[`home-assistant/core` › `homeassistant/components/solaredge`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/solaredge)
(stand van release **2026.11.0**).

De integratie gebruikt hetzelfde domein (`solaredge`) als de ingebouwde versie.
Home Assistant laadt een custom integratie met hetzelfde domein **in plaats van**
de ingebouwde versie. Bestaande SolarEdge-configuraties blijven daardoor gewoon
werken. In het log verschijnt dan de normale waarschuwing dat een custom
integratie de ingebouwde versie vervangt.

### Verschillen met de core-integratie

- De client voor de SolarEdge Monitoring API zit in de integratie zelf
  ([`api.py`](custom_components/solaredge/api.py)), gebaseerd op
  [aiosolaredge](https://github.com/bdraco/aiosolaredge) 1.0.2 (MIT-licentie).
  Het pip-pakket `aiosolaredge` is daardoor niet meer nodig.
- Inloggen met gebruikersnaam en wachtwoord van het webportaal (pakket
  `solaredge-web`, productiestatistieken per paneel) is verwijderd. De
  integratie werkt alleen nog met een API-sleutel en heeft geen externe
  Python-pakketten nodig.
- Bestaande configuraties worden automatisch gemigreerd (config entry-versie
  1.2): een opgeslagen gebruikersnaam en wachtwoord worden verwijderd.
  Een configuratie die alleen een web-login had, start niet meer. Configureer
  die opnieuw met een API-sleutel. Eerder geïmporteerde statistieken per paneel
  blijven in de database staan.

## Vereisten

- Home Assistant **2026.11.0** of nieuwer (de code gebruikt API's en
  Python 3.14-syntax uit die versie).

## Installatie

### Via HACS (custom repository)

1. HACS → menu rechtsboven → *Custom repositories*.
2. Voeg de URL van deze repository toe met type **Integration**.
3. Installeer **SolarEdge (custom)** en herstart Home Assistant.

### Handmatig

1. Kopieer de map `custom_components/solaredge` naar
   `<config>/custom_components/solaredge`.
2. Herstart Home Assistant.

Daarna: *Instellingen → Apparaten & diensten → Integratie toevoegen → SolarEdge*.

## Configuratie

Geef het site-ID en de API-sleutel op. De API-sleutel maak je aan in het
SolarEdge-monitoringportaal. Daarmee krijg je de sensoren van de site
(vermogen, energie, opslag, power flow, enz.).

## Bijwerken vanuit Home Assistant core

1. Kopieer de bestanden uit `homeassistant/components/solaredge` van de gewenste
   release-tag naar `custom_components/solaredge`, behalve `strings.json`.
   Vervang daarna `from aiosolaredge import SolarEdge` door
   `from .api import SolarEdge` en haal `aiosolaredge` weg uit `requirements` en
   `loggers` in `manifest.json`. Haal ook de web-login weer weg:
   `SolarEdgeModulesCoordinator`, de `web_auth`-sectie in de config flow,
   `solaredge-web` en de `recorder`-afhankelijkheid. Behoud de migratie naar
   versie 1.2 in `__init__.py`.
2. Zet `strings.json` om naar `translations/en.json`. Vervang daarbij elke
   `[%key:...%]`-verwijzing door de tekst zelf (zie `homeassistant/strings.json`
   in core), want custom integraties lossen die verwijzingen niet op.
3. Zet `version` in `manifest.json` en `homeassistant` in `hacs.json` op de nieuwe
   release.

## Licentie

Apache License 2.0. Zie [LICENSE.md](LICENSE.md). De oorspronkelijke code komt
van de Home Assistant-auteurs.
