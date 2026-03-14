# Proven StrongARM Comparator Parameters

These values come from the `sky130-comparator` project (Score 1.00/1.00).
Validated across 30 PVT corners + 200 Monte Carlo samples.

## Mapping to ADC parameters

| Comparator param | Proven value | ADC parameter name |
|-----------------|-------------|-------------------|
| Win (input pair W) | 50.0 um | Wcomp_in |
| Lin (input pair L) | 1.0 um | Lcomp_in |
| Wlatn/Wlatp (latch W) | 1.0 um | Wcomp_latch |
| Llatn/Llatp (latch L) | 0.5 um | Lcomp_latch |
| Wtail (tail W) | 25.0 um | Wcomp_tail |
| Wrst (reset W) | 3.0 um | Fixed at 2.0 um in ADC design.cir |

## Key results
- Offset: 2.32 mV at 4.5 sigma MC (spec < 5 mV)
- Delay: 0.32 ns nominal
- Power: 8.2 uW nominal

## Recommendation
Start with these values. The comparator is already proven.
Focus optimization on Cu (unit capacitance) and Tsar_ns (SAR clock period).
The comparator sizes may need to be SMALLER for the ADC since:
- ADC doesnt need 50um input pair (offset is less critical -- the DAC sets accuracy)
- Smaller comparator = lower power = faster = better for SAR
