# Verification Plan — Every Block Must Prove It Works

Each block must include **mandatory testbenches** that produce waveform plots and numerical results. No block is "done" until all testbenches pass and plots are saved in `plots/`.

The README.md of each block must show all plots inline (like the comparator project). A reviewer reading only README.md should see the full proof.

---

## Bitcell Testbenches

### TB1: Write & Store
- Write a logic 1 into the cell via WWL + BLW/BLBW
- Release write signals
- Verify Q=VDD, QB=0 after 10ns settling
- Repeat for logic 0
- **Plot:** `plots/write_waveforms.png` — WWL, BLW, BLBW, Q, QB vs time

### TB2: Read Current (Weight=1)
- Store weight=1 in cell
- Precharge BL to VDD
- Assert WL=VDD
- Measure current from BL through read port to VSS
- **Plot:** `plots/read_current_w1.png` — WL, BL voltage, I(BL) vs time
- **Measure:** I_READ in µA, T_READ (time to 90% of steady-state)

### TB3: Leakage (Weight=0)
- Store weight=0 in cell
- Precharge BL to VDD
- Assert WL=VDD
- Measure leakage current
- **Plot:** `plots/read_current_w0.png` — same signals, showing near-zero current
- **Measure:** I_LEAK in nA

### TB4: ON/OFF Ratio
- Compute I_READ / I_LEAK
- **Must be > 100** for usable compute accuracy

### TB5: Static Noise Margin (Butterfly Curve)
- Sweep voltage sources in the cross-coupled inverter loop
- Plot the two VTC curves
- SNM = largest square that fits inside the butterfly eye
- **Plot:** `plots/snm_butterfly.png` — classic butterfly curve with SNM box

### TB6: Read Disturb
- Store weight=1
- Apply 1000 consecutive read pulses (WL high/low cycles)
- Verify Q and QB haven't flipped
- **Plot:** `plots/read_disturb.png` — Q, QB over 1000 cycles (or summary)

### TB7: Current Summation (Critical CIM Test)
- Connect 8 cells to one bitline
- Program weights = [1, 0, 1, 1, 0, 0, 1, 0] (4 active cells)
- Apply WL=VDD to all 8 rows simultaneously
- Measure total BL current
- Verify: I_total ≈ 4 × I_READ (within 5%)
- **Plot:** `plots/current_summation.png` — BL current vs time, annotated with expected value
- **Plot:** `plots/summation_linearity.png` — measured current vs number of active cells (should be linear)

### TB8: Pulse-Width Modulation Response
- Store weight=1
- Apply WL pulses of varying width: 1ns, 2ns, 5ns, 10ns, 20ns
- Measure charge deposited on BL (= integral of current)
- Verify charge is proportional to pulse width (linear)
- **Plot:** `plots/charge_vs_pulsewidth.png` — charge vs pulse width (should be straight line)

---

## SAR ADC Testbenches

### TB1: DC Transfer Curve
- Ramp input voltage from 0 to VDD in 1mV steps
- Record output code at each step
- **Plot:** `plots/adc_transfer_curve.png` — output code vs input voltage (staircase)

### TB2: DNL (Differential Non-Linearity)
- From the transfer curve, compute code width for each code
- DNL[k] = (width[k] / LSB_ideal) - 1
- **Plot:** `plots/adc_dnl.png` — DNL vs code, with ±0.5 LSB spec lines
- **Measure:** worst-case DNL

### TB3: INL (Integral Non-Linearity)
- INL = cumulative sum of DNL
- **Plot:** `plots/adc_inl.png` — INL vs code, with ±1.0 LSB spec lines
- **Measure:** worst-case INL

### TB4: Missing Codes Check
- Verify every code (0 to 63) appears at least once in the ramp test
- Report any missing codes

### TB5: Comparator Waveforms
- Show the StrongARM comparator operating during one SAR conversion
- **Plot:** `plots/comparator_in_adc.png` — comparator inputs, outputs, clock during 6 cycles

### TB6: Conversion Timing
- Measure time from sample command to valid output
- **Plot:** `plots/adc_timing.png` — sample clock, internal SAR bits resolving, final output

### TB7: Power Measurement
- Measure average supply current during one conversion
- Compute energy per conversion

---

## PWM Driver Testbenches

### TB1: All 16 Codes
- Apply each input code (0 to 15) one at a time
- Measure actual output pulse width for each
- **Plot:** `plots/pwm_all_codes.png` — overlay of all 16 output waveforms
- **Plot:** `plots/pwm_linearity.png` — measured pulse width vs input code (should be linear)

### TB2: Linearity Error
- Compute deviation from ideal line for each code
- **Plot:** `plots/pwm_error.png` — error (ns) vs code
- **Measure:** max linearity error as percentage

### TB3: Rise/Fall Time
- Measure 10-90% rise time and 90-10% fall time
- Test with 100fF capacitive load (representing 64 cell gates)
- **Plot:** `plots/pwm_edges.png` — zoomed view of rising and falling edges

### TB4: Drive Strength
- Sweep load capacitance from 10fF to 500fF
- Measure rise time vs load
- **Plot:** `plots/pwm_drive_strength.png` — rise time vs load capacitance

---

## Array Testbenches

### TB1: Single Column Dot Product
- 8 cells, 1 column
- Program weights = [1, 0, 1, 1, 0, 0, 1, 0]
- Apply input vector = [3, 7, 1, 15, 0, 4, 8, 2] (as pulse widths)
- Measure BL voltage after compute
- Expected dot product: 3×1 + 7×0 + 1×1 + 15×1 + 0×0 + 4×0 + 8×1 + 2×0 = 27
- Compare analog result vs 27
- **Plot:** `plots/single_column_waveforms.png` — WL pulses, BL discharge, annotated result

### TB2: Precharge Verification
- Precharge all bitlines to VDD
- Verify all reach VDD within T_precharge
- Release precharge
- Verify bitlines hold (no droop without compute)
- **Plot:** `plots/precharge_waveforms.png` — precharge signal, multiple BL voltages

### TB3: Full MVM (8×8)
- Random 8×8 binary weight matrix W
- Random 8-element 4-bit input vector x
- Compute analog result y_analog from bitline voltages
- Compute digital reference y_numpy = W @ x (numpy)
- Compare element by element
- **Plot:** `plots/mvm_scatter.png` — y_analog vs y_numpy for all outputs (should be on y=x line)
- **Plot:** `plots/mvm_error_histogram.png` — histogram of per-element errors
- **Measure:** RMSE, max error

### TB4: Linearity Test
- Fix weights to all-ones column
- Sweep input from 0 to 15 on one row (others at 0)
- BL voltage should decrease linearly with input value
- **Plot:** `plots/array_linearity.png` — BL voltage vs input code

### TB5: Multi-Vector Test
- Run 10 different random (weight, input) pairs
- Collect RMSE for each
- **Plot:** `plots/mvm_accuracy_distribution.png` — box plot or bar chart of RMSE across tests

### TB6: Worst Case — All Weights Active
- Set all 64 weights to 1, apply maximum input (15) on all rows
- This produces maximum BL discharge
- Verify BL doesn't go negative or hit a floor
- Verify ADC input range is sufficient
- **Plot:** `plots/worst_case_discharge.png` — BL voltage for max-current case

---

## Integration Testbenches

### TB1: End-to-End MVM (SPICE)
- Small 8×8 array, full signal chain: PWM → Array → ADC
- Known inputs and weights
- Compare final digital ADC output vs numpy calculation
- **Plot:** `plots/e2e_waveforms.png` — complete signal chain in one plot

### TB2: MNIST Single Digit
- Load one MNIST image ("7")
- Process through binary neural network
- Show activations at each stage
- **Plot:** `plots/mnist_single_digit.png` — input image, layer outputs, final classification

### TB3: MNIST Accuracy (Behavioral Model)
- Run 1000 MNIST test images through calibrated behavioral model
- **Plot:** `plots/mnist_accuracy.png` — accuracy vs number of test images
- **Plot:** `plots/mnist_confusion_matrix.png` — 10×10 confusion matrix

### TB4: MNIST Examples Grid
- Show 25 random test images with their classifications
- Green border = correct, red border = incorrect
- **Plot:** `plots/mnist_examples.png` — 5×5 grid

### TB5: Analog vs Digital Comparison
- For 100 MVM operations, compare SPICE result vs behavioral model vs numpy
- **Plot:** `plots/analog_vs_digital.png` — three-way scatter plot

---

## Plot Requirements

All plots must:
1. Be saved as PNG at 150 DPI minimum in the block's `plots/` directory
2. Have clear axis labels and titles
3. Include spec lines where applicable (e.g., DNL ±0.5 LSB)
4. Use a consistent, readable style
5. Be referenced in README.md with `![Description](plots/filename.png)`
