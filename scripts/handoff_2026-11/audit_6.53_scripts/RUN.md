# Воспроизведение аудита 6.00–6.53

Входы: репозиторий `spb-arcticdem-pipeline` @ c1b6d12, релиз v1.1
`spb_terra_fusion_6.00_A2.tif` (473 МБ), `download/mosaic_6.00/spb_terra_conf_6.00_A2.tif`
(в репо), репозиторий `spb-arcticdem-pipeline-5.3` (паспорт 202). RAM ≥ 6 ГБ.

```bash
F=spb_terra_fusion_6.00_A2.tif; C=$REPO/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif
python3 fusion_seams.py $F $C                 # ступени data|fill на опубликованном растре
python3 zone_seams.py   $F $C $REPO           # ступени на границах 84 зон
python3 indep.py        $F $C $REPO $REPO53   # независимые отметки по классам и тирам
python3 repro_prep.py   $F $C $REPO $WD       # сетка 8 м, unified (1 060 684), Лаплас g0 (~10 мин)
python3 repro_protocols.py $WD $REPO          # P0/P1/P2/P3 + проверка копии + срез 2 км
python3 boundary_decomp.py $WD $REPO          # откуда ступень data|fill
python3 plot_loo.py $REPO/download/atayanc_6.53/marks_di_653.csv   # участок: in-sample vs held-out
```

Контроль копии: `repro_vs_published_A2` в protocols_result.json — медиана 0.000,
MAD 0.003 м по 5.2 млн ячеек; P1 воспроизводит MAD отчёта 6.00 до 3-го знака.
