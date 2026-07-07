# Predbat

![image](https://github.com/springfall2008/batpred/actions/workflows/code-quality.yml/badge.svg)
![image](https://github.com/springfall2008/batpred/actions/workflows/publish-docs.yml/badge.svg)
![image](https://github.com/springfall2008/batpred/actions/workflows/pages/pages-build-deployment/badge.svg)

## About this fork

This is a personal fork of [springfall2008/batpred](https://github.com/springfall2008/batpred) with the following additions on top of upstream (currently based on upstream v8.44.0):

### Feed-in Tariff (FIT) support

UK FIT scheme support for solar self-consumption optimisation. FIT is off by default; when the `metric_fit_enable` master switch is turned on and `metric_fit_generation_rate` is set (Expert Mode), the optimiser treats actual export as having no additional value (deemed export pays regardless), prefers self-consumption of solar, and leaves battery headroom for forecast solar rather than grid-charging to 100%. Turning `metric_fit_enable` off disables all FIT behaviour while leaving the configured rates in place. Adds config items `metric_fit_enable`, `metric_fit_generation_rate`, `metric_fit_deemed_export_rate` and `metric_fit_deemed_export_percentage`, plus sensors `predbat.fit_income`, `predbat.fit_income_best` and `predbat.fit_income_yesterday`. FIT logic is also implemented in the C++ prediction kernel (fork ABI revision 102) with parity tests, and all six platform kernel binaries are rebuilt with FIT support.

### Custom web dashboard

The built-in web UI (port 5052) gains a `/dash_entities` page and a redesigned power flow diagram.

### Fork-based releases and self-update

This fork publishes its own GitHub releases (versioned `v712.xx`) via a release workflow that derives the tag from `THIS_VERSION` in `apps/predbat/predbat.py` on push to `main`. Predbat's built-in updater points at this fork, so installations tracking this repository self-update from these releases.

### Fixes

* Database history now returns correct results for entities with no state change inside the query window
* FIT payment calculator bug fixes
* cspell dictionary ordering fixed to match the pre-commit hook

### Fork agent docs

`CLAUDE.md`, `AGENTS.md`, `CODEX.md` and `GEMINI.md` provide guidance for AI coding agents working in this repository, including fork-specific constraints (kernel ABI revision rules, release process).

Everything below is the upstream README.

## Introduction

Home battery prediction and automatic charging for Home Assistant supporting multiple inverters, including GivEnergy, Solis, Huawei, SolarEdge, SigEnergy, FoxESS, Sofar, Tesla Powerwall and many more.

Also known by some as Batpred or Batman!

![icon](https://github.com/springfall2008/batpred/assets/48591903/7c207423-1423-4f88-beb2-d1da5cfbfeeb) ![image](https://github.com/springfall2008/batpred/assets/48591903/e98a0720-d2cf-4b71-94ab-97fe09b3cee1)

If you want to buy me a beer, then please use [Paypal](https://paypal.me/predbat?country.x=GB&locale.x=en_GB) or [GitHub sponsor](https://github.com/springfall2008)
![image](https://github.com/springfall2008/batpred/assets/48591903/b3a533ef-0862-4e0b-b272-30e254f58467)

* Use my referral code for Octopus Energy: <https://share.octopus.energy/jolly-eel-176>
* Use my referral code for Axle Energy (UK): <https://vpp.axle.energy/landing/grid?ref=R-VWIICRSA>

If you find Home Assistant and Predbat too difficult to set up yourself, there is now [PredBat Cloud](https://predbat.com/), a paid version of Predbat hosted in the cloud. Please note that while I have given permission for PredBat Cloud to operate under license, PredBat will remain open source for personal use.

## Predbat documentation

You can find the latest Predbat documentation at [https://springfall2008.github.io/batpred/](https://springfall2008.github.io/batpred/) and
how-to videos on my [YouTube channel](https://www.youtube.com/@springfall2008).

The documentation covers how Predbat works and how to get it installed
and configured, video tutorials and FAQs to help you get going.
It also explains how you can contribute to the project.

## Support

For support, please raise a GitHub ticket or use the Facebook Group: [Predbat](https://www.facebook.com/groups/1477599886299106)

Some inverters have their own groups also, e.g.:

* [GivTCP](https://www.facebook.com/groups/615579009972782)
* [Solis](https://www.facebook.com/groups/288045168816481)

## License

Please see [License](https://github.com/springfall2008/batpred/blob/main/License.md)

```text
Copyright (c) Trefor Southwell 2025-2026 - All rights reserved
This software may be used at no cost for personal use only.
No warranty is given, either expressed or implied.
```
