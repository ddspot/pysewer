.. SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
.. SPDX-License-Identifier: GPL-3.0-only

About
=======
Pysewer provides a framework to automatically generate cost-efficient sewer network layouts using minimal data requirements. 

It is build around an algorithm for generation of viable sewer-network layouts. The approximated sewer network is represented by sources (households/buildings), potential pathways, and one or multiple sinks. 
The algorithm approximates the directed Steiner tree (the Steiner arborescence) between all sources and the sink by using an repeated shortest path heuristic (RSPH).  


Key features
------------
- Automatic generation of gravity-flow prioritised sewer network layouts
- Minimal data requirements
- Sewer network visualisation
- Export of the sewer network for preliminary planning and analysis


Who is it for?
--------------
Pysewer is for anyone who wants to generate a sewer network layout with minimal data requirements.
It is designed for sanitary engineers, critical infrastructure planners, researchers who need to generate sewer network layouts for preliminary planning and analysis.


Development team
----------------
Pysewer is developed by the working group on Water-Sensitive Infrastructure Planning (WASP) at the Helmholtz Centre for Environmental Research (UFZ) in Leipzig, Germany.