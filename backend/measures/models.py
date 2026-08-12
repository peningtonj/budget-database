from django.db import models


class ProgramExpense(models.Model):
    """Mirrors the program_expenses table built by build_db.py / parse_pbs.py.

    Portfolio -> Agency -> Outcome -> Program -> fiscal year -> amount.
    program_name is the stable cross-year identifier (not program_number,
    which gets reused for different programs across years).
    """

    edition = models.TextField()
    budget_year = models.TextField()
    portfolio = models.TextField()
    agency = models.TextField()
    outcome_number = models.IntegerField()
    outcome_description = models.TextField(null=True)
    program_number = models.TextField()
    program_name = models.TextField(null=True)
    fiscal_year = models.TextField()
    estimate_type = models.TextField()
    amount_thousands = models.IntegerField()
    source_file = models.TextField()
    sheet_name = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "program_expenses"


class MeasureImpact(models.Model):
    """Mirrors measure_impacts: one row per (measure, agency, direction,
    fiscal year) with the combined $ profile for that group."""

    measure_id = models.TextField()  # stable 8-digit id, see build_measures_db.py
    edition = models.TextField()
    measure_name = models.TextField()
    portfolio = models.TextField()
    agency = models.TextField()
    direction = models.TextField()  # 'payment' | 'receipt'
    fiscal_year = models.TextField()
    amount_thousands = models.IntegerField()
    source_file = models.TextField()

    class Meta:
        managed = False
        db_table = "measure_impacts"


class MeasureProgram(models.Model):
    """Mirrors measure_programs: the distinct programs touched by one
    (measure, agency, direction) group. program_number is raw -- join to
    ProgramExpense on (budget_year, agency, program_number) to resolve a
    name, since numbers get reused for different programs over time.
    is_departmental=1 with program_number ending "X.0" means "Departmental
    (Outcome X)" as a whole, not a real numbered program."""

    measure_id = models.TextField()
    edition = models.TextField()
    measure_name = models.TextField()
    portfolio = models.TextField()
    agency = models.TextField()
    direction = models.TextField()
    program_number = models.TextField()
    is_departmental = models.BooleanField()
    source_file = models.TextField()

    class Meta:
        managed = False
        db_table = "measure_programs"


class AgencyAlias(models.Model):
    """Mirrors agency_aliases, built by build_agency_aliases.py: for one
    (budget_year, source file), pairs the agency's own formal name (from
    its Table 1.1 "resource statement" title) with that same file's
    filename-derived short form (clean_agency() -- what
    ProgramExpense.agency uses). Both names come from the identical file,
    so this pairing is exact, not a heuristic guess -- use it to resolve
    a measure's agency string (typically the formal name, typed into a
    Table 1.2 sheet) to the short form program_expenses.agency uses for
    the same year."""

    edition = models.TextField()
    budget_year = models.TextField()
    portfolio = models.TextField()
    formal_name = models.TextField()
    short_name = models.TextField()
    source_file = models.TextField()

    class Meta:
        managed = False
        db_table = "agency_aliases"


class MeasureText(models.Model):
    """Mirrors measure_text, built by build_bp2_db.py from Budget Paper
    No. 2's own narrative measure write-ups (parse_bp2.py) -- a
    different source from the per-agency PBS Table 1.2 data the other
    models above mirror. Looked up by (measure_name, edition) alone;
    portfolio is stored for display only, not part of the lookup key.

    Covers 21 editions, 18 of which have no measure_impacts/
    measure_programs row at all (those only cover 3 -- see
    MeasureImpact/MeasureProgram above); measure_id (see measure_id.py)
    is what makes those 18 editions' measures reachable by URL/search
    despite that, independent of whether PBS data exists for them."""

    measure_id = models.TextField()  # stable 8-digit id, see measure_id.py
    measure_name = models.TextField()
    edition = models.TextField()
    portfolio = models.TextField()
    document_section = models.TextField()  # 'payment' | 'receipt' | 'capital'
    source_page = models.IntegerField()
    full_measure_text = models.TextField()
    source_file = models.TextField()

    class Meta:
        managed = False
        db_table = "measure_text"


class MeasureTextComponent(models.Model):
    measure_text = models.ForeignKey(
        MeasureText, on_delete=models.DO_NOTHING, db_column="measure_text_id"
    )
    ordinal = models.IntegerField()
    level = models.IntegerField()  # 1 | 2
    marker = models.TextField()  # 'dot' | 'dash'
    parent_ordinal = models.IntegerField(null=True)
    text = models.TextField()

    class Meta:
        managed = False
        db_table = "measure_text_component"


class MeasureTextRelated(models.Model):
    measure_text = models.ForeignKey(
        MeasureText, on_delete=models.DO_NOTHING, db_column="measure_text_id"
    )
    ordinal = models.IntegerField()
    phrase = models.TextField()

    class Meta:
        managed = False
        db_table = "measure_text_related"


class MeasureTextHeadlineFinancial(models.Model):
    measure_text = models.ForeignKey(
        MeasureText, on_delete=models.DO_NOTHING, db_column="measure_text_id"
    )
    impact_type = models.TextField()  # 'Receipt' | 'Payment' | 'Capital'
    is_related = models.BooleanField()
    department_name = models.TextField()
    year_index = models.IntegerField()
    value_kind = models.TextField()  # 'numeric' | 'special'
    value_numeric_million = models.FloatField(null=True)
    value_raw = models.TextField()

    class Meta:
        managed = False
        db_table = "measure_text_headline_financial"
