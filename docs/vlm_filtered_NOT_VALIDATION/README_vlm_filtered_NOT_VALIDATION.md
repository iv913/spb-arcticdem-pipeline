# VLM-filtered pools — NOT FOR VALIDATION

**WARNING**: Files matching `*_vlm_filtered_NOT_VALIDATION.csv` were filtered
using VLM (vision language model) that saw the model surface. This violates
the validation rule §1.1 of AGENT_HANDOFF_2026-11.md:

> "фильтровать контрольные точки по |dH| от модели или по согласию с ней —
> ни порогом, ни триажем, ни VLM, который видит dH или модельную высоту"

The VLM filter removed points where:
- VLM diagnosed "building" / "tree" / "water" on satellite imagery
- But VLM also saw the model elevation surface (left side of comparison image)
- The filter rate correlated with |dH| (40% rejected at |dH|<0.25m, 72% at 2-4m)

**These pools are NOT suitable for accuracy assessment.**
They are preserved only as documentation of the VLM filter experiment.

For proper validation, use `validation_pool_v9` (built per task B4) which:
- Uses ground marks WITHOUT VLM filter
- Uses ICESat by tracks (RGT), with VLM applied blindly (no dH in query)
- Includes atayanc_pool_v2 (188 confirmed by two independent readings)
