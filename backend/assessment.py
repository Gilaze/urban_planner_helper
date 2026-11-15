from collections import Counter
from itertools import islice

from .datasf import ASSESSOR_URL, LOT_KEY, DataSF, ProviderUnavailable
from .schemas import NonNegative, StrictModel

ASSESSOR_SOURCE = "https://data.sf.gov/d/wv5m-vpq2"


class Assessment(StrictModel):
    parcel_number: str
    closed_roll_year: int
    assessed_land_value: NonNegative | None = None
    assessed_improvement_value: NonNegative | None = None


def enrich_assessments(parcels, eligible_ids, year: int, api: DataSF, url: str = ASSESSOR_URL):
    counts = Counter(apn for parcel in parcels for apn in parcel.properties.source_apns)
    eligible = [parcel for parcel in parcels if parcel.id in eligible_ids]
    apns = sorted({
        apn for parcel in eligible for apn in parcel.properties.source_apns
        if counts[apn] == 1 and LOT_KEY.fullmatch(apn)
    })
    assessments = {}
    duplicate = set()
    apn_iterator = iter(apns)
    for group in iter(lambda: tuple(islice(apn_iterator, 200)), ()):
        quoted_apns = ", ".join(f"'{apn}'" for apn in group)
        query = (
            "SELECT parcel_number, closed_roll_year, assessed_land_value, assessed_improvement_value "
            f"WHERE closed_roll_year = {year} AND parcel_number IN ({quoted_apns}) "
            "ORDER BY parcel_number, row_id, :id"
        )
        for row in api.paginate(url, query):
            assessment = Assessment.model_validate(row)
            apn = assessment.parcel_number
            if apn not in group or assessment.closed_roll_year != year:
                raise ProviderUnavailable("DataSF returned a row outside the assessor query")
            if apn in assessments:
                duplicate.add(apn)
            assessments[apn] = assessment
    for apn in duplicate:
        del assessments[apn]

    result = []
    joins = 0
    for parcel in parcels:
        if parcel.id not in eligible_ids:
            result.append(parcel)
            continue
        properties = parcel.properties
        records = [assessments.get(apn) for apn in properties.source_apns]
        flags = list(properties.quality_flags)
        if not records or any(record is None for record in records):
            flags.append("assessor_apn_join_incomplete")
            land = improvements = assessment_year = None
        else:
            joins += 1
            land = _total(records, "assessed_land_value")
            improvements = _total(records, "assessed_improvement_value")
            assessment_year = year
            if land is None or improvements is None:
                flags.append("assessor_values_incomplete")
        result.append(parcel.model_copy(update={"properties": properties.model_copy(update={
            "assessed_land_usd": land,
            "assessed_improvement_usd": improvements,
            "assessment_year": assessment_year,
            "quality_flags": flags,
        })}))
    return result, joins


def _total(records, field):
    values = [getattr(row, field) for row in records]
    return sum(values) if all(value is not None for value in values) else None
