---
# Elective Module Allocation Optimization

This version guarantees the following key principles and system behaviors.
---

## Key Guarantees

### 1. Maximum Allocation

- Maximizes the total number of allocated elective module slots.
- If a feasible allocation exists where **every student receives all allowed modules**, the algorithm will find it.

### 2. Preference-Ranked Optimization

Among all solutions that achieve the maximum possible allocation, the algorithm optimizes student preferences using a **lexicographic preference objective**:

1. Maximize the number of **Rank 1** allocations.
2. Maximize the number of **Rank 2** allocations.
3. Maximize the number of **Rank 3** allocations.
4. Continue in the same manner for subsequent preference ranks.

---

## Constraints

The existing constraints remain in place:

- **Elective Capacity:**

$$\text{capacity} = \text{number\_of\_classes} \times 25$$

- **Module Quota:** Each course retains its designated module quota.
- **Uniqueness:** Each student can receive each elective at most once.

---

## Optimization Model

The allocator models the problem as a **Bipartite Matching / Minimum-Cost Maximum-Flow (MCMF) Network**:

$$\text{Source} \longrightarrow \text{Student} \longrightarrow \text{Elective} \longrightarrow \text{Sink}$$

### Network Definitions

| Edge                               | Capacity                | Description / Weight                                                                 |
| ---------------------------------- | ----------------------- | ------------------------------------------------------------------------------------ |
| **Source $\rightarrow$ Student**   | Max modules allowed     | Limits total allocations per student.                                                |
| **Student $\rightarrow$ Elective** | `1`                     | Assigns a specific elective to a student. Cost is assigned based on preference rank. |
| **Elective $\rightarrow$ Sink**    | Total elective capacity | Caps total enrolled students per elective.                                           |

---

## Optimization Strategy

```
[ Step 1: Maximize Flow ]  ---> Ensures maximum possible elective slots are filled
            │
            ▼
[ Step 2: Minimize Cost ]  ---> Lexicographically optimizes preference ranks

```

1. **Max-Flow Priority:** The algorithm first sends the maximum possible flow through the network, ensuring that the overall count of allocated module slots is maximized.
2. **Cost Minimization:** It then minimizes the preference cost strictly among all valid maximum-flow solutions.
3. **Lexicographic Weighting:** The preference cost function is structured so that the optimizer strictly prefers:

- More **Rank 1** allocations,
- Then more **Rank 2** allocations,
- Then more **Rank 3** allocations, and so on.

> **Summary:** Allocation quantity takes absolute priority over preference optimization, while student preferences are optimized as strongly as possible within that maximum-allocation solution space.
