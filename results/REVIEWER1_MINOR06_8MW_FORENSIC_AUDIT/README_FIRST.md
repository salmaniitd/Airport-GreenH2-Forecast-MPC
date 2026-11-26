# Reviewer 1 Minor Comment 6: Frozen 8 MW Causal MPC LP diagnosis

This audit replays only the Causal MPC 8 MW stress case. Neither controller
source, model parameters, forecasts nor held-out dataset are modified.

- Exact original failure reproduced: **True**
- Observed failure step index: **16497** (archived 16497)
- Failure date: **2025-12-16 06:45:00**
- Original error: **MPC decision failed at step_index=16497: HiGHS MPC failed after all recovery attempts. highs-ds/presolve=True: status=2; The problem is infeasible. (HiGHS Status 8: model_status is Infeasible; primal_status is None); highs-ds/presolve=False: status=2; The problem is infeasible. (HiGHS Status 8: model_status is Infeasible; primal_status is Infeasible); highs-ipm/presolve=False: status=2; The problem is infeasible. (HiGHS Status 8: model_status is Infeasible; primal_status is Infeasible); highs/presolve=True: status=2; The problem is infeasible. (HiGHS Status 8: model_status is Infeasible; primal_status is None)**

The one-decision counterfactual feasibility tests isolate potentially
binding constraints, but are not new dispatch results. Preserve the original
failed status. An alternative feasible state does not prove that the 8 MW
policy would be feasible over the entire campaign.

Raw HiGHS LP matrices are included for an independent mathematical audit.