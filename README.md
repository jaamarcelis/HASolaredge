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

Geef het site-ID op en minstens één van de twee manieren van inloggen:

- **API-sleutel**: voor de sensoren van de site (vermogen, energie, opslag,
  power flow, enz.).
- **Gebruikersnaam en wachtwoord van het webportaal**: voor productiestatistieken
  per paneel (module). Die worden als externe statistieken in de recorder
  geïmporteerd.

## Bijwerken vanuit Home Assistant core

1. Kopieer de bestanden uit `homeassistant/components/solaredge` van de gewenste
   release-tag naar `custom_components/solaredge`, behalve `strings.json`.
2. Zet `strings.json` om naar `translations/en.json`. Vervang daarbij elke
   `[%key:...%]`-verwijzing door de tekst zelf (zie `homeassistant/strings.json`
   in core), want custom integraties lossen die verwijzingen niet op.
3. Zet `version` in `manifest.json` en `homeassistant` in `hacs.json` op de nieuwe
   release.

## Licentie

Apache License 2.0. Zie [LICENSE.md](LICENSE.md). De oorspronkelijke code komt
van de Home Assistant-auteurs.
