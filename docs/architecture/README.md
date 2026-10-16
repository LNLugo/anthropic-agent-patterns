# Architecture

The standards, decisions and review process that govern the modules in this
repository, and that a COE would publish for delivery teams.

| Document | What it is |
|---|---|
| [Reference architecture](reference-architecture.md) | One picture of how the modules fit, each layer's control and the number that shows it works |
| [Standards](standards.md) | AI, application, API and integration standards, each naming the tests that enforce it, and the exception register |
| [Decision records](adr/README.md) | The decisions behind the standards: options rejected, evidence, and the strongest objection |
| [Design review checklist](design-review-checklist.md) | The questions asked at review, mapped to the standards, with the evidence to bring |
| [COE charter](coe-charter.md) | Decision rights, forums, when a review is required, exceptions, training and metrics |
| [Conformance scorecard](scorecard.md) | Every standard against every module it applies to: tested, checked, excepted or review-only, computed by [conformance.py](conformance.py) |

The standards and exceptions are YAML ([standards.yaml](standards.yaml),
[exceptions.yaml](exceptions.yaml)), rendered into standards.md by
`python docs/architecture/render_standards.py`. `tests/test_architecture.py`
keeps these documents honest:
- standards.md must be current;
- every test a standard cites must exist;
- every ADR and standard reference must resolve;
- every standard must be in the checklist;
- every exception must name a real standard and a module it applies to, and
  expire within six months;
- every relative link and anchor must resolve.

`tests/test_conformance.py` is the governance gate. It fails CI on a standard a
module doesn't meet with no registered exception, on a static check that fails,
on an expired exception, and on a stale one (an exception for something that now
conforms must be closed). The published scorecard must match what the check
finds.

The first run found 5 unregistered gaps:
- **3 were evidence that existed but wasn't mapped** to the module it covered.
- **1 was real and is now closed:** 12's traces had never been through the leak
  check. Adding them found the first version of the check was blind to 12's
  content, which is fixed.
- **1 is real and now registered:** 07's answer evaluation has one run and no
  baseline (EXC-004).
