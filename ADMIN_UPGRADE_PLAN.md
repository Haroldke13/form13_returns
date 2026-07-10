# Admin and Data Workflow Upgrade Plan

Status key: `done`, `partial`, `next`

1. `done` Enforce `is_authorized` during login.
2. `done` Track failed login attempts and last failed login time.
3. `done` Track last successful login time and IP.
4. `done` Add admin account-authorization routes.
5. `done` Add revoke-account route with self-protection.
6. `done` Persist admin deadlines in the database.
7. `done` Add workflow status to reports.
8. `done` Add review status and reviewer metadata to reports.
9. `done` Add return reason and review notes support.
10. `done` Add duplicate flag support on reports.
11. `done` Add file metadata table for uploaded documents.
12. `done` Add import batch tracking table.
13. `done` Add import row error tracking table.
14. `done` Add field-level change log table.
15. `done` Record field-level report changes during admin/user updates.
16. `done` Replace session-heavy admin view with persistent dashboard data.
17. `done` Add audit log filters and CSV export.
18. `done` Add workflow review queue to admin dashboard.
19. `done` Add report file upload action from admin dashboard.
20. `done` Add CSV/XLSX import route for core report fields.
21. `partial` Reduce monolithic form-processing code into shared services and finish deeper schema normalization for legacy text date fields / database-level uniqueness constraints.

## Follow-up for item 21

- Extract shared report create/update logic from `app.py` into service functions or forms/schemas.
- Convert legacy text-based admin date fields to typed `Date`/`DateTime` columns with explicit migrations.
- Add safe uniqueness constraints for registration identifiers after a duplicate-cleanup pass.
- Add tests for auth gating, import batches, workflow transitions, and audit exports.



decimal fields command ////<input
  type="text"
  name="donor_amount[]"
  inputmode="decimal"
  placeholder="1,234.56"
  pattern="^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$">



<!-- build this outside js//// spotted one subtle thing while reviewing the patch: the asset-row template is built inside a JavaScript template string, so its regex backslashes need to be escaped there. I’m correcting that before I run checks.

Explored 1 file, 1 search
Edited file
form14.html
+1
-1

            <input type="text" name="assets_value[]" class="asset-value" oninput="calculateAssetsTotals()"
                inputmode="decimal" pattern="^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?$">
                inputmode="decimal" pattern="^(?:\\d+|\\d{1,3}(?:,\\d{3})+)(?:\\.\\d{1,2})?$">
        </td>






 -->