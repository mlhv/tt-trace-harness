# Preserve Workflow Business-Tag Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SkyWalking `ActiveSpan.tag()` calls to `ts-preserve-service`'s `PreserveServiceImpl.preserve()` so the trace for every preserve workflow run carries the business data (trip, security check, seat availability, price, seat allocation, order outcome) that explains its shape — without capturing full request/response payloads.

**Architecture:** All changes live in one file, `PreserveServiceImpl.java`, because it's a pure orchestrator that already holds the request and response for every downstream call as local variables. Six `ActiveSpan.tag()` insertion points, each independently testable. `PreserveServiceImplTest.java` is extended incrementally: the existing `testPreserve()` happy-path test grows a `MockedStatic<ActiveSpan>` block that accumulates one verification per task; each early-return branch (security fail, seat not-enough, second-class allocation) gets its own smaller dedicated test.

**Tech Stack:** Java 8, Spring Boot 2.3.12.RELEASE, JUnit 4, Mockito (+ `mockito-inline` for static mocking of `ActiveSpan`), SkyWalking `apm-toolkit-trace` 8.6.0 (version already proven in this codebase, used by `ts-travel-service`).

**Spec:** `tt-trace-harness/docs/superpowers/specs/2026-09-21-business-tag-enrichment-design.md`

## Global Constraints

- Repo for all code changes: `~/train-ticket` (a separate checkout from `tt-trace-harness`). All file paths below are relative to `~/train-ticket` unless stated otherwise.
- **No Java/Maven/Docker toolchain is available in an automated agent session** (confirmed: no `java`/`mvn` binary, and `docker` requires a `sudo` password that isn't available non-interactively). **Every "run the tests" / "build" step in this plan must be executed by Minh**, either in their own terminal or by relaying the exact command back through the `!` prefix when working with an agent. Do not attempt these commands unattended — they will fail with a permission or "command not found" error that no retry fixes.
- `apm-toolkit-trace` version is pinned at `8.6.0` to match the working precedent already in `ts-travel-service/pom.xml`. Do not guess a different version.
- `mockito-inline` version is set to `3.6.28` as a starting point (first version comfortably past the 3.4.0 minimum for `Mockito.mockStatic()`, Java 8 compatible). This has **not** been verified against this project's actual resolved `mockito-core` version (managed transitively by `spring-boot-starter-parent:2.3.12.RELEASE`, not pinned in this repo, and not independently confirmable without running Maven). Task 1's first verification run is the check — if it fails with a `mockito-core`/`mockito-inline` version mismatch, pin both to the same explicit version instead of just adding `mockito-inline` unpinned to the other.
- No task pushes a Docker image or changes anything running in the shared `default` namespace cluster. Task 7 stops at "image built locally" — pushing and redeploying (`kubectl set image` / helm upgrade) is a manual, explicitly-confirmed step for Minh to run separately, since it affects a shared research cluster other people may be using.
- Every `ActiveSpan.tag(key, value)` call in this plan uses the two-`String` overload. Non-`String` values (`oti.getSeatType()` is `int`, `cor.getStatus()` is `Integer`) are wrapped with `String.valueOf(...)`.

---

## File Structure

- **Modify:** `ts-preserve-service/pom.xml` — add `apm-toolkit-trace` (compile) and `mockito-inline` (test) dependencies.
- **Modify:** `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java` — add the `ActiveSpan` import and six tag-call sites inside `preserve()`.
- **Modify:** `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java` — add `ActiveSpan`/`MockedStatic` imports, extend `testPreserve()` with tag verification, add four new test methods (`testPreserve_securityCheckFailed`, `testPreserve_seatNotEnough_firstClass`, `testPreserve_seatNotEnough_secondClass`, `testPreserve_secondClassSeatAllocation`).

---

### Task 1: Toolkit dependency + root workflow tags

**Files:**
- Modify: `ts-preserve-service/pom.xml`
- Modify: `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:1-48`
- Test: `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java`

**Interfaces:**
- Produces: the `ActiveSpan` import and `MockedStatic<ActiveSpan>` test pattern that Tasks 2-6 extend. Root tags `workflow`, `tripId`, `seatTypeRequested` fire unconditionally at the top of `preserve()`, before any downstream call.

- [ ] **Step 1: Add dependencies to `ts-preserve-service/pom.xml`**

Add inside the existing `<dependencies>` block (after the `jakarta.validation-api` dependency, before `</dependencies>`):

```xml
        <dependency>
            <groupId>org.apache.skywalking</groupId>
            <artifactId>apm-toolkit-trace</artifactId>
            <version>8.6.0</version>
        </dependency>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-inline</artifactId>
            <version>3.6.28</version>
            <scope>test</scope>
        </dependency>
```

- [ ] **Step 2: Write the failing test**

In `PreserveServiceImplTest.java`, add imports (alongside the existing `import org.mockito.*` lines):

```java
import org.apache.skywalking.apm.toolkit.trace.ActiveSpan;
import org.mockito.MockedStatic;
```

**Pre-existing fixture bug to fix first:** `testPreserve()`'s `travelResult` is built with only `setPrices(...)` called (around line 99-102). `PreserveServiceImpl.preserve()` unconditionally calls `resultForTravel.getRoute().getStations()` and, for the first-class branch, `resultForTravel.getTrainType().getConfortClass()` — both `null` in the current fixture, so the test NPEs before reaching any of the code this plan touches. This is not caused by this plan's changes, but it must be fixed for `testPreserve()` to run to completion at all. Change:

```java
        //response for travel result
        TravelResult travelResult = new TravelResult();
        travelResult.setPrices( new HashMap<String, String>(){{ put("confortClass", "1.0"); }} );
```

to:

```java
        //response for travel result
        TravelResult travelResult = new TravelResult();
        travelResult.setRoute(new Route());
        travelResult.setTrainType(new TrainType());
        travelResult.setPrices( new HashMap<String, String>(){{ put("confortClass", "1.0"); }} );
```

(`Route` and `TrainType` are already covered by the existing `import edu.fudan.common.entity.*;`.)

Replace the existing exercise+assert lines at the end of `testPreserve()`:

```java
        Response result = preserveServiceImpl.preserve(oti, headers);
        Assert.assertEquals(new Response<>(1, "Success.", null), result);
```

with:

```java
        try (MockedStatic<ActiveSpan> activeSpan = Mockito.mockStatic(ActiveSpan.class)) {
            Response result = preserveServiceImpl.preserve(oti, headers);
            Assert.assertEquals(new Response<>(1, "Success.", null), result);

            activeSpan.verify(() -> ActiveSpan.tag("workflow", "preserve"));
            activeSpan.verify(() -> ActiveSpan.tag("tripId", "G1255"));
            activeSpan.verify(() -> ActiveSpan.tag("seatTypeRequested", "2"));
        }
```

- [ ] **Step 3: Run the test and confirm it fails for the right reason**

Run (Minh, in a terminal with Java 8 + Maven, from `~/train-ticket`):
```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest#testPreserve
```
Expected: compiles, then fails — either a `MockitoException` about the static mock maker not being enabled (if `mockito-inline` didn't resolve as expected — see Global Constraints' note on version pinning) or, if the mock infra works, a `WantedButNotInvoked` failure for `ActiveSpan.tag("workflow", "preserve")` because `preserve()` doesn't call it yet.

If it's the mock-maker error, stop and resolve the `mockito-inline`/`mockito-core` version pinning before continuing — every later task depends on this working. If instead you see a `NullPointerException` pointing at `PreserveServiceImpl.preserve()`'s `resultForTravel.getRoute()` or `.getTrainType()`, the Step 2 fixture fix (`setRoute`/`setTrainType`) didn't get applied — go back and add it; nothing past that point in `preserve()` can run without it.

- [ ] **Step 4: Implement the root tags**

In `PreserveServiceImpl.java`, add the import after the existing `edu.fudan.common.entity.*` import (line 19):

```java
import org.apache.skywalking.apm.toolkit.trace.ActiveSpan;
```

Change the start of `preserve()` (line 48) from:

```java
    public Response preserve(OrderTicketsInfo oti, HttpHeaders headers) {
        //1.detect ticket scalper
```

to:

```java
    public Response preserve(OrderTicketsInfo oti, HttpHeaders headers) {
        ActiveSpan.tag("workflow", "preserve");
        ActiveSpan.tag("tripId", oti.getTripId());
        ActiveSpan.tag("seatTypeRequested", String.valueOf(oti.getSeatType()));
        //1.detect ticket scalper
```

- [ ] **Step 5: Run the test and confirm it passes**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest#testPreserve
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd ~/train-ticket
git add ts-preserve-service/pom.xml ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java
git commit -m "Add apm-toolkit-trace and tag preserve() workflow/tripId/seatType"
```

---

### Task 2: Security-check tag

**Files:**
- Modify: `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:52-56`
- Test: `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java`

**Interfaces:**
- Consumes: the `MockedStatic<ActiveSpan>` pattern from Task 1.
- Produces: `security.status` = `"pass"` or `"fail"` on every preserve trace.

- [ ] **Step 1: Write the failing tests**

In `testPreserve()`, add one more verify call inside the existing `try (MockedStatic<ActiveSpan> activeSpan = ...)` block, after the `seatTypeRequested` verify:

```java
            activeSpan.verify(() -> ActiveSpan.tag("workflow", "preserve"));
            activeSpan.verify(() -> ActiveSpan.tag("tripId", "G1255"));
            activeSpan.verify(() -> ActiveSpan.tag("seatTypeRequested", "2"));
            activeSpan.verify(() -> ActiveSpan.tag("security.status", "pass"));
```

Add a new test method for the failure branch (this exercises far less of `preserve()`, since it returns immediately):

```java
    @Test
    public void testPreserve_securityCheckFailed() {
        OrderTicketsInfo oti = OrderTicketsInfo.builder()
                .accountId(UUID.randomUUID().toString())
                .contactsId(UUID.randomUUID().toString())
                .from("from_station")
                .to("to_station")
                .date(StringUtils.Date2String(new Date()))
                .tripId("G1255")
                .seatType(2)
                .build();

        Response securityFail = new Response<>(0, "Security check failed", null);
        ResponseEntity<Response> reSecurityFail = new ResponseEntity<>(securityFail, HttpStatus.OK);
        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(Class.class)))
                .thenReturn(reSecurityFail);

        try (MockedStatic<ActiveSpan> activeSpan = Mockito.mockStatic(ActiveSpan.class)) {
            Response result = preserveServiceImpl.preserve(oti, headers);
            Assert.assertEquals(new Response<>(0, "Security check failed", null), result);

            activeSpan.verify(() -> ActiveSpan.tag("security.status", "fail"));
            activeSpan.verify(() -> ActiveSpan.tag(Mockito.eq("seat.checkResult"), Mockito.anyString()), Mockito.never());
        }
    }
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest
```
Expected: `testPreserve` fails on the new `security.status` verify (not called yet); `testPreserve_securityCheckFailed` compiles and fails the same way.

- [ ] **Step 3: Implement the security tag**

In `PreserveServiceImpl.java`, change:

```java
        Response result = checkSecurity(oti.getAccountId(), headers);
        if (result.getStatus() == 0) {
```

to:

```java
        Response result = checkSecurity(oti.getAccountId(), headers);
        ActiveSpan.tag("security.status", result.getStatus() == 0 ? "fail" : "pass");
        if (result.getStatus() == 0) {
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/train-ticket
git add ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java
git commit -m "Tag preserve()'s security check outcome"
```

---

### Task 3: Seat-availability check tags

**Files:**
- Modify: `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:78-100`
- Test: `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java`

**Interfaces:**
- Consumes: `MockedStatic<ActiveSpan>` pattern from Task 1; the `security.status` tag from Task 2 must still fire before this gate (fixture already passes security).
- Produces: `seat.confortAvailable`, `seat.economyAvailable` on every trace that reaches this point; `seat.checkResult` = `"pass"` or `"not_enough"`.

- [ ] **Step 1: Write the failing tests**

In `testPreserve()`'s `MockedStatic<ActiveSpan>` block, add after the `security.status` verify:

```java
            activeSpan.verify(() -> ActiveSpan.tag("seat.confortAvailable", "1"));
            activeSpan.verify(() -> ActiveSpan.tag("seat.economyAvailable", "0"));
            activeSpan.verify(() -> ActiveSpan.tag("seat.checkResult", "pass"));
```

(These values come from the existing fixture: `tripResponse.setConfortClass(1)` is already set at line 88 of the test; `economyClass` is never set, so it defaults to `0`.)

Add two new test methods for the "not enough" branches. Both need `checkSecurity` and `getContactsById` to succeed first (two calls), then `getTripAllDetailInformation` to return a trip with insufficient capacity:

```java
    @Test
    public void testPreserve_seatNotEnough_firstClass() {
        OrderTicketsInfo oti = OrderTicketsInfo.builder()
                .accountId(UUID.randomUUID().toString())
                .contactsId(UUID.randomUUID().toString())
                .from("from_station")
                .to("to_station")
                .date(StringUtils.Date2String(new Date()))
                .tripId("G1255")
                .seatType(2)
                .build();

        Response securityPass = new Response<>(1, null, null);
        ResponseEntity<Response> reSecurityPass = new ResponseEntity<>(securityPass, HttpStatus.OK);
        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(Class.class)))
                .thenReturn(reSecurityPass);

        Contacts contacts = new Contacts();
        contacts.setDocumentNumber("document_number");
        contacts.setName("name");
        contacts.setDocumentType(1);
        Response<Contacts> contactsResponse = new Response<>(1, null, contacts);
        ResponseEntity<Response<Contacts>> reContacts = new ResponseEntity<>(contactsResponse, HttpStatus.OK);

        TripResponse tripResponse = new TripResponse();
        tripResponse.setConfortClass(0);
        TripAllDetail tripAllDetail = new TripAllDetail(true, "message", tripResponse, new Trip());
        Response<TripAllDetail> tripDetailResponse = new Response<>(1, null, tripAllDetail);
        ResponseEntity<Response<TripAllDetail>> reTripDetail = new ResponseEntity<>(tripDetailResponse, HttpStatus.OK);

        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(ParameterizedTypeReference.class)))
                .thenReturn(reContacts).thenReturn(reTripDetail);

        try (MockedStatic<ActiveSpan> activeSpan = Mockito.mockStatic(ActiveSpan.class)) {
            Response result = preserveServiceImpl.preserve(oti, headers);
            Assert.assertEquals(new Response<>(0, "Seat Not Enough", null), result);

            activeSpan.verify(() -> ActiveSpan.tag("seat.confortAvailable", "0"));
            activeSpan.verify(() -> ActiveSpan.tag("seat.checkResult", "not_enough"));
            activeSpan.verify(() -> ActiveSpan.tag(Mockito.eq("price.confortClass"), Mockito.any()), Mockito.never());
        }
    }

    @Test
    public void testPreserve_seatNotEnough_secondClass() {
        OrderTicketsInfo oti = OrderTicketsInfo.builder()
                .accountId(UUID.randomUUID().toString())
                .contactsId(UUID.randomUUID().toString())
                .from("from_station")
                .to("to_station")
                .date(StringUtils.Date2String(new Date()))
                .tripId("G1255")
                .seatType(3)
                .build();

        Response securityPass = new Response<>(1, null, null);
        ResponseEntity<Response> reSecurityPass = new ResponseEntity<>(securityPass, HttpStatus.OK);
        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(Class.class)))
                .thenReturn(reSecurityPass);

        Contacts contacts = new Contacts();
        contacts.setDocumentNumber("document_number");
        contacts.setName("name");
        contacts.setDocumentType(1);
        Response<Contacts> contactsResponse = new Response<>(1, null, contacts);
        ResponseEntity<Response<Contacts>> reContacts = new ResponseEntity<>(contactsResponse, HttpStatus.OK);

        TripResponse tripResponse = new TripResponse();
        tripResponse.setConfortClass(0);
        tripResponse.setEconomyClass(3);
        TripAllDetail tripAllDetail = new TripAllDetail(true, "message", tripResponse, new Trip());
        Response<TripAllDetail> tripDetailResponse = new Response<>(1, null, tripAllDetail);
        ResponseEntity<Response<TripAllDetail>> reTripDetail = new ResponseEntity<>(tripDetailResponse, HttpStatus.OK);

        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(ParameterizedTypeReference.class)))
                .thenReturn(reContacts).thenReturn(reTripDetail);

        try (MockedStatic<ActiveSpan> activeSpan = Mockito.mockStatic(ActiveSpan.class)) {
            Response result = preserveServiceImpl.preserve(oti, headers);
            Assert.assertEquals(new Response<>(0, "Seat Not Enough", null), result);

            activeSpan.verify(() -> ActiveSpan.tag("seat.economyAvailable", "3"));
            activeSpan.verify(() -> ActiveSpan.tag("seat.checkResult", "not_enough"));
        }
    }
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest
```
Expected: `testPreserve` fails on the new verifies; the two new tests compile and fail (no tags exist yet).

- [ ] **Step 3: Implement the seat-check tags**

In `PreserveServiceImpl.java`, change the `else` block and the fallthrough after it (originally lines 84-100):

```java
        } else {
            TripResponse tripResponse = gtdr.getTripResponse();
            //LOGGER.info("TripResponse:" + tripResponse.toString());
            if (oti.getSeatType() == SeatClass.FIRSTCLASS.getCode()) {
                if (tripResponse.getConfortClass() == 0) {
                    PreserveServiceImpl.LOGGER.warn("[preserve][Step 3][Check seat][Check seat is enough][TripId: {}]",oti.getTripId());
                    return new Response<>(0, "Seat Not Enough", null);
                }
            } else {
                if (tripResponse.getEconomyClass() == SeatClass.SECONDCLASS.getCode() && tripResponse.getConfortClass() == 0) {
                    PreserveServiceImpl.LOGGER.warn("[preserve][Step 3][Check seat][Check seat is Not enough][TripId: {}]",oti.getTripId());
                    return new Response<>(0, "Seat Not Enough", null);
                }
            }
        }
        Trip trip = gtdr.getTrip();
```

to:

```java
        } else {
            TripResponse tripResponse = gtdr.getTripResponse();
            ActiveSpan.tag("seat.confortAvailable", String.valueOf(tripResponse.getConfortClass()));
            ActiveSpan.tag("seat.economyAvailable", String.valueOf(tripResponse.getEconomyClass()));
            //LOGGER.info("TripResponse:" + tripResponse.toString());
            if (oti.getSeatType() == SeatClass.FIRSTCLASS.getCode()) {
                if (tripResponse.getConfortClass() == 0) {
                    PreserveServiceImpl.LOGGER.warn("[preserve][Step 3][Check seat][Check seat is enough][TripId: {}]",oti.getTripId());
                    ActiveSpan.tag("seat.checkResult", "not_enough");
                    return new Response<>(0, "Seat Not Enough", null);
                }
            } else {
                if (tripResponse.getEconomyClass() == SeatClass.SECONDCLASS.getCode() && tripResponse.getConfortClass() == 0) {
                    PreserveServiceImpl.LOGGER.warn("[preserve][Step 3][Check seat][Check seat is Not enough][TripId: {}]",oti.getTripId());
                    ActiveSpan.tag("seat.checkResult", "not_enough");
                    return new Response<>(0, "Seat Not Enough", null);
                }
            }
        }
        ActiveSpan.tag("seat.checkResult", "pass");
        Trip trip = gtdr.getTrip();
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/train-ticket
git add ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java
git commit -m "Tag preserve()'s seat-availability check outcome"
```

---

### Task 4: Price tags

**Files:**
- Modify: `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:127-139`
- Test: `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java`

**Interfaces:**
- Consumes: `MockedStatic<ActiveSpan>` pattern from Task 1.
- Produces: `price.confortClass`, `price.economyClass` from the `basic/travel` response, tagged regardless of which tier is later selected.

- [ ] **Step 1: Write the failing test**

The existing fixture only sets `confortClass` in the prices map (`travelResult.setPrices(new HashMap<String, String>(){{ put("confortClass", "1.0"); }});`, around line 100 of the test). Update it to also carry an `economyClass` price, so both new tags have a non-null value to assert:

```java
        travelResult.setPrices( new HashMap<String, String>(){{ put("confortClass", "1.0"); put("economyClass", "0.5"); }} );
```

Add to `testPreserve()`'s `MockedStatic<ActiveSpan>` block, after the `seat.checkResult` verify:

```java
            activeSpan.verify(() -> ActiveSpan.tag("price.confortClass", "1.0"));
            activeSpan.verify(() -> ActiveSpan.tag("price.economyClass", "0.5"));
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest#testPreserve
```
Expected: FAIL — price tags not yet called.

- [ ] **Step 3: Implement the price tags**

In `PreserveServiceImpl.java`, change:

```java
        TravelResult resultForTravel = re.getBody().getData();

        order.setSeatClass(oti.getSeatType());
```

to:

```java
        TravelResult resultForTravel = re.getBody().getData();
        ActiveSpan.tag("price.confortClass", resultForTravel.getPrices().get("confortClass"));
        ActiveSpan.tag("price.economyClass", resultForTravel.getPrices().get("economyClass"));

        order.setSeatClass(oti.getSeatType());
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest#testPreserve
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/train-ticket
git add ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java
git commit -m "Tag preserve()'s resolved price data"
```

---

### Task 5: Seat allocation tags

**Files:**
- Modify: `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:148-166`
- Test: `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java`

**Interfaces:**
- Consumes: `MockedStatic<ActiveSpan>` pattern from Task 1.
- Produces: `seat.allocatedClass`, `seat.allocatedNumber` from `dipatchSeat()`'s result, at both the first-class and second-class call sites.

- [ ] **Step 1: Write the failing tests**

Add to `testPreserve()`'s `MockedStatic<ActiveSpan>` block, after the price verifies (the existing fixture uses `seatType(2)`, i.e. first class, and `ticket.setSeatNo(1)`):

```java
            activeSpan.verify(() -> ActiveSpan.tag("seat.allocatedClass", "FirstClassSeat"));
            activeSpan.verify(() -> ActiveSpan.tag("seat.allocatedNumber", "1"));
```

Add a new test for the second-class branch, which the existing fixture never exercises. This needs the full happy-path chain (checkSecurity → contacts → trip detail → basic/travel → seat dispatch), with `seatType(3)` and trip capacity that passes the Task 3 gate:

```java
    @Test
    public void testPreserve_secondClassSeatAllocation() {
        OrderTicketsInfo oti = OrderTicketsInfo.builder()
                .accountId(UUID.randomUUID().toString())
                .contactsId(UUID.randomUUID().toString())
                .from("from_station")
                .to("to_station")
                .date(StringUtils.Date2String(new Date()))
                .tripId("G1255")
                .seatType(3)
                .assurance(0)
                .foodType(0)
                .build();

        Response okResponse = new Response<>(1, null, null);
        ResponseEntity<Response> reOk = new ResponseEntity<>(okResponse, HttpStatus.OK);
        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(Class.class)))
                .thenReturn(reOk);

        Contacts contacts = new Contacts();
        contacts.setDocumentNumber("document_number");
        contacts.setName("name");
        contacts.setDocumentType(1);
        Response<Contacts> contactsResponse = new Response<>(1, null, contacts);
        ResponseEntity<Response<Contacts>> reContacts = new ResponseEntity<>(contactsResponse, HttpStatus.OK);

        TripResponse tripResponse = new TripResponse();
        tripResponse.setConfortClass(1);
        tripResponse.setEconomyClass(1);
        tripResponse.setStartTime(StringUtils.Date2String(new Date()));
        TripAllDetail tripAllDetail = new TripAllDetail(true, "message", tripResponse, new Trip());
        Response<TripAllDetail> tripDetailResponse = new Response<>(1, null, tripAllDetail);
        ResponseEntity<Response<TripAllDetail>> reTripDetail = new ResponseEntity<>(tripDetailResponse, HttpStatus.OK);

        TravelResult travelResult = new TravelResult();
        travelResult.setRoute(new Route());
        travelResult.setTrainType(new TrainType());
        travelResult.setPrices( new HashMap<String, String>(){{ put("confortClass", "1.0"); put("economyClass", "0.5"); }} );
        Response<TravelResult> travelResultResponse = new Response<>(1, null, travelResult);
        ResponseEntity<Response<TravelResult>> reTravelResult = new ResponseEntity<>(travelResultResponse, HttpStatus.OK);

        Ticket ticket = new Ticket();
        ticket.setSeatNo(7);
        Response<Ticket> ticketResponse = new Response<>(1, null, ticket);
        ResponseEntity<Response<Ticket>> reTicket = new ResponseEntity<>(ticketResponse, HttpStatus.OK);

        Order order = new Order();
        order.setId(UUID.randomUUID().toString());
        Response<Order> orderResponse = new Response<>(1, null, order);
        ResponseEntity<Response<Order>> reOrder = new ResponseEntity<>(orderResponse, HttpStatus.OK);

        User user = new User();
        user.setEmail("email");
        user.setUserName("user_name");
        Response<User> userResponse = new Response<>(1, null, user);
        ResponseEntity<Response<User>> reUser = new ResponseEntity<>(userResponse, HttpStatus.OK);

        Mockito.when(restTemplate.exchange(
                Mockito.anyString(),
                Mockito.any(HttpMethod.class),
                Mockito.any(HttpEntity.class),
                Mockito.any(ParameterizedTypeReference.class)))
                .thenReturn(reContacts).thenReturn(reTripDetail).thenReturn(reTravelResult)
                .thenReturn(reTicket).thenReturn(reOrder).thenReturn(reUser);

        try (MockedStatic<ActiveSpan> activeSpan = Mockito.mockStatic(ActiveSpan.class)) {
            Response result = preserveServiceImpl.preserve(oti, headers);
            Assert.assertEquals(new Response<>(1, "Success.", null), result);

            activeSpan.verify(() -> ActiveSpan.tag("seat.allocatedClass", "SecondClassSeat"));
            activeSpan.verify(() -> ActiveSpan.tag("seat.allocatedNumber", "7"));
        }
    }
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest
```
Expected: `testPreserve` fails on the new verifies; `testPreserve_secondClassSeatAllocation` compiles and fails.

- [ ] **Step 3: Implement the seat allocation tags**

In `PreserveServiceImpl.java`, change the seat-dispatch if/else (originally lines 148-166):

```java
        if (oti.getSeatType() == SeatClass.FIRSTCLASS.getCode()) {
            int firstClassTotalNum = resultForTravel.getTrainType().getConfortClass();
            Ticket ticket =
                    dipatchSeat(oti.getDate(),
                            order.getTrainNumber(), fromStationName, toStationName,
                            SeatClass.FIRSTCLASS.getCode(), firstClassTotalNum, stationList, headers);
            order.setSeatNumber("" + ticket.getSeatNo());
            order.setSeatClass(SeatClass.FIRSTCLASS.getCode());
            order.setPrice(resultForTravel.getPrices().get("confortClass"));
        } else {
            int secondClassTotalNum = resultForTravel.getTrainType().getEconomyClass();
            Ticket ticket =
                    dipatchSeat(oti.getDate(),
                            order.getTrainNumber(), fromStationName, toStationName,
                            SeatClass.SECONDCLASS.getCode(), secondClassTotalNum, stationList, headers);
            order.setSeatClass(SeatClass.SECONDCLASS.getCode());
            order.setSeatNumber("" + ticket.getSeatNo());
            order.setPrice(resultForTravel.getPrices().get("economyClass"));
        }
```

to:

```java
        if (oti.getSeatType() == SeatClass.FIRSTCLASS.getCode()) {
            int firstClassTotalNum = resultForTravel.getTrainType().getConfortClass();
            Ticket ticket =
                    dipatchSeat(oti.getDate(),
                            order.getTrainNumber(), fromStationName, toStationName,
                            SeatClass.FIRSTCLASS.getCode(), firstClassTotalNum, stationList, headers);
            ActiveSpan.tag("seat.allocatedClass", SeatClass.FIRSTCLASS.getName());
            ActiveSpan.tag("seat.allocatedNumber", String.valueOf(ticket.getSeatNo()));
            order.setSeatNumber("" + ticket.getSeatNo());
            order.setSeatClass(SeatClass.FIRSTCLASS.getCode());
            order.setPrice(resultForTravel.getPrices().get("confortClass"));
        } else {
            int secondClassTotalNum = resultForTravel.getTrainType().getEconomyClass();
            Ticket ticket =
                    dipatchSeat(oti.getDate(),
                            order.getTrainNumber(), fromStationName, toStationName,
                            SeatClass.SECONDCLASS.getCode(), secondClassTotalNum, stationList, headers);
            ActiveSpan.tag("seat.allocatedClass", SeatClass.SECONDCLASS.getName());
            ActiveSpan.tag("seat.allocatedNumber", String.valueOf(ticket.getSeatNo()));
            order.setSeatClass(SeatClass.SECONDCLASS.getCode());
            order.setSeatNumber("" + ticket.getSeatNo());
            order.setPrice(resultForTravel.getPrices().get("economyClass"));
        }
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/train-ticket
git add ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java
git commit -m "Tag preserve()'s seat allocation outcome"
```

---

### Task 6: Order creation tags

**Files:**
- Modify: `ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java:170-175`
- Test: `ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java`

**Interfaces:**
- Consumes: `MockedStatic<ActiveSpan>` pattern from Task 1.
- Produces: `order.id`, `order.price`, `order.status` — the workflow's terminal business outcome.

- [ ] **Step 1: Write the failing test**

`testPreserve()`'s fixture builds its own `order` object (around line 111) purely as the mocked return value of `createOrder()` — capture its id in a local variable so the test can assert against it precisely. Change:

```java
        //response for createOrder()
        Order order = new Order();
        order.setId(UUID.randomUUID().toString());
```

to:

```java
        //response for createOrder()
        Order order = new Order();
        String expectedOrderId = UUID.randomUUID().toString();
        order.setId(expectedOrderId);
```

Add to the `MockedStatic<ActiveSpan>` block, after the seat allocation verifies:

```java
            activeSpan.verify(() -> ActiveSpan.tag("order.id", expectedOrderId));
            activeSpan.verify(() -> ActiveSpan.tag("order.price", "1.0"));
            activeSpan.verify(() -> ActiveSpan.tag("order.status", "1"));
```

(`order.price` is `"1.0"` because the fixture's `seatType(2)` selects `confortClass`, tagged `"1.0"` per Task 4's fixture.)

- [ ] **Step 2: Run the test and confirm it fails**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest#testPreserve
```
Expected: FAIL — order tags not yet called.

- [ ] **Step 3: Implement the order tags**

In `PreserveServiceImpl.java`, change:

```java
        Response<Order> cor = createOrder(order, headers);
        if (cor.getStatus() == 0) {
            PreserveServiceImpl.LOGGER.error("[preserve][Step 4][Do Order][Create Order Fail][OrderId: {},  Reason: {}]", order.getId(), cor.getMsg());
            return new Response<>(0, cor.getMsg(), null);
        }
        PreserveServiceImpl.LOGGER.info("[preserve][Step 4][Do Order][Do Order Complete]");
```

to:

```java
        Response<Order> cor = createOrder(order, headers);
        if (cor.getStatus() == 0) {
            PreserveServiceImpl.LOGGER.error("[preserve][Step 4][Do Order][Create Order Fail][OrderId: {},  Reason: {}]", order.getId(), cor.getMsg());
            return new Response<>(0, cor.getMsg(), null);
        }
        ActiveSpan.tag("order.id", cor.getData().getId());
        ActiveSpan.tag("order.price", order.getPrice());
        ActiveSpan.tag("order.status", String.valueOf(cor.getStatus()));
        PreserveServiceImpl.LOGGER.info("[preserve][Step 4][Do Order][Do Order Complete]");
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
mvn -pl ts-preserve-service -am test -Dtest=PreserveServiceImplTest#testPreserve
```
Expected: PASS.

- [ ] **Step 5: Run the full test suite for the module**

```bash
mvn -pl ts-preserve-service -am test
```
Expected: all tests PASS, including the pre-existing `testDipatchSeat`, `testSendEmail`, `testGetAccount`, and `PreserveControllerTest` — none of this plan's changes touch those code paths.

- [ ] **Step 6: Commit**

```bash
cd ~/train-ticket
git add ts-preserve-service/src/main/java/preserve/service/PreserveServiceImpl.java ts-preserve-service/src/test/java/preserve/service/PreserveServiceImplTest.java
git commit -m "Tag preserve()'s order creation outcome"
```

---

### Task 7: Build a local image (manual redeploy handoff)

**Files:** none (build/packaging only, no source changes).

**Interfaces:**
- Consumes: the fully tagged `PreserveServiceImpl.java` from Tasks 1-6.
- Produces: a local Docker image (`ts-preserve-service:business-tags`) ready for Minh to push and deploy — this task does not push or deploy it.

- [ ] **Step 1: Package the jar**

```bash
cd ~/train-ticket
mvn -pl ts-preserve-service -am clean package
```
Expected: `BUILD SUCCESS`, producing `ts-preserve-service/target/ts-preserve-service-1.0.jar`.

- [ ] **Step 2: Build the Docker image**

```bash
cd ~/train-ticket
docker build -t ts-preserve-service:business-tags ts-preserve-service
```
Expected: image builds successfully from `ts-preserve-service/Dockerfile` (base `java:8-jre`, copies the jar built in Step 1).

- [ ] **Step 3: Verify the image tags actually get emitted**

Run the image locally, pointed at nothing real (it will fail to register with the discovery service, but should start far enough to prove the jar is valid):
```bash
docker run --rm -p 14568:14568 ts-preserve-service:business-tags
```
Expected: Spring Boot startup logs, no `ClassNotFoundException`/`NoClassDefFoundError` for `org.apache.skywalking.apm.toolkit.trace.ActiveSpan` (would indicate `apm-toolkit-trace` didn't get bundled correctly — check it's `compile` scope, not `test`, in `pom.xml`). `Ctrl+C` to stop once startup logs settle.

- [ ] **Step 4: STOP — manual step, do not automate**

Pushing this image to the registry the cluster pulls from (`codewisdom/ts-preserve-service`) and redeploying it (`kubectl set image deployment/ts-preserve-service ...` or a Helm upgrade) changes a shared research cluster other people may be using. This is for **Minh to run directly**, not something an agent session should do unattended:

```bash
# reference only — run these yourself, after deciding on an image tag/registry:
docker tag ts-preserve-service:business-tags <your-registry>/ts-preserve-service:business-tags
docker push <your-registry>/ts-preserve-service:business-tags
kubectl set image deployment/ts-preserve-service ts-preserve-service=<your-registry>/ts-preserve-service:business-tags -n default
```

After redeploying, confirm the new tags are landing by running the `tt-trace-harness` smoke test (`.venv/bin/python scripts/smoke_test_preserve.py`, from the `tt-trace-harness` repo) and checking that `spans[].tags` in the resulting output includes `workflow`/`tripId`/`security.status`/etc. — no `tt-trace-harness` code changes are needed for this, since `client_sw.py` already requests all tags generically.
