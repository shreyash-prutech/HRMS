# ADR-014: Pluggable Payment Provider Abstraction

## Status

Accepted

## Context

The checkout service needs to support multiple payment providers across regions without requiring code changes for each rollout. Today, Stripe is the existing compatibility path, but we need a clean abstraction that allows the service to route requests to different providers such as Adyen or Razorpay based on business rules.

The primary goals are:

- decouple checkout flow logic from any single payment gateway implementation
- support regional provider selection
- enable gradual provider rollout behind feature flags
- preserve existing Stripe behavior exactly for the current contract suite

## Decision

We will introduce a `PaymentProvider` interface that defines the payment contract used by checkout:

- `authorize`
- `capture`
- `void`
- `refund`
- `verify_webhook_signature`

All provider implementations must conform to this interface so that checkout can invoke a provider through a common adapter surface.

Provider selection will be based on two inputs:

- `country_code`
- the `payments.provider_v2` feature flag

Selection rules:

- Stripe remains the default provider for all regions
- when `payments.provider_v2` is enabled, India and Brazil route to Adyen
- India is identified by `IN` and Brazil by `BR`
- countries outside the rollout scope continue to use Stripe
- provider selection must be deterministic and driven by the country code plus the feature flag only

Razorpay is supported as a future provider through the same abstraction, but it is not part of the initial routing rollout unless it is explicitly added to the provider registry and selection rules later.

## Rationale

Using a shared interface allows the checkout service to swap providers without changing the business flow. This keeps provider-specific behavior isolated behind adapters and reduces the risk of coupling payment orchestration to one gateway SDK.

Selecting by `country_code` plus feature flag gives us:

- explicit regional control
- safe progressive rollout
- the ability to keep Stripe as the default compatibility path
- a clear way to enable Adyen only where intended

The `payments.provider_v2` flag acts as the operational switch for the new routing behavior. With the flag off, Stripe is used everywhere. With the flag on, India and Brazil can be moved to Adyen while other regions continue on Stripe.

## Stripe Compatibility

Stripe remains the default and backwards-compatible path.

The Stripe adapter must preserve existing behavior byte-for-byte for the current payments contract suite. Any refactor introducing the abstraction must not change request shaping, response semantics, webhook verification behavior, or other externally observable Stripe behavior.

This ADR requires the Stripe implementation to remain the baseline compatibility path until the contract suite confirms parity.

## Consequences

Positive outcomes:

- provider implementations can be added without modifying checkout orchestration logic
- regional rollouts can be controlled through feature flags
- the abstraction supports future expansion to additional providers
- existing Stripe behavior remains the stable fallback

Trade-offs:

- selection logic becomes slightly more complex
- feature-flag governance becomes important to avoid unintended regional routing
- provider adapters must remain faithful to the payment contract to preserve compatibility

## Alternatives Considered

### Hardcode provider branching in checkout logic

Rejected because it would entangle business logic with provider-specific behavior and make future rollouts harder to maintain.

### Environment-only provider selection

Rejected because it would not support region-specific rollout decisions and would be too coarse for staged enablement.

### Provider-specific logic scattered across the codebase

Rejected because it would fragment payment handling, increase duplication, and make contract compliance difficult to verify.

## Rollout Plan

1. Introduce the `PaymentProvider` interface and provider registry.
2. Keep Stripe as the default provider.
3. Enable `payments.provider_v2` for a limited rollout in India and Brazil.
4. Verify Stripe contract-suite behavior remains unchanged.
5. Expand provider coverage only through explicit registry and selection updates.

## Related Decision

This ADR documents the architectural basis for the pluggable payment provider abstraction and the India/Brazil Adyen rollout behind `payments.provider_v2`.
