import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np
from shapely.geometry import Point, box, shape
from shapely.strtree import STRtree

from backend.api import create_app
from backend.build_planning import assign_area, load_tracts, summarize_area_needs
from backend.geometry import polygon_model, unproject
from backend.planning import Planning, need_level
from backend.planning_schemas import AreaNeed, AreaProperties, PlanningArea, PlanningIndex, PlanningSources, SiteContext, SiteFitAssumptions
from backend.release import ROOT, Release, ReleaseInputs, freeze
from backend.economics import load_assumptions
from backend.schemas import CATEGORIES, ParcelFeature, ParcelProperties, Point as ModelPoint
from backend.site_fit import open_ground, rectangles, suggest_size


def assumptions():
    return SiteFitAssumptions.model_validate_json((ROOT / 'config/site-fit.json').read_bytes())


def context(parcel_id='sf:small', width=40, length=25, geometry=None):
    geometry = box(550000, 4180000, 550000 + width, 4180000 + length) if geometry is None else geometry
    return SiteContext(parcel_id=parcel_id, area_id='06075010100', available_geometry=polygon_model(unproject(geometry)) if not geometry.is_empty else None,
                       buildings_checked=True, existing_building_count=0)


def design(site, category, parcel_area=None):
    ground = open_ground(site)
    programs = assumptions()
    return suggest_size(site, programs.categories[category], rectangles(ground, (parcel_area or ground.area) * programs.max_lot_coverage), ground.area)


def sources():
    return PlanningSources(tract_url='https://example.test/tracts', tract_sha256='test', tract_vintage='2021',
                           population_vintage='2024', building_source=None, demographic_sha256='test')


class SizeTests(unittest.TestCase):
    def test_multi_floor_clinic_and_compact_grocery_have_different_fit(self):
        site = context()
        clinic, grocery, school = (design(site, category) for category in ('clinic', 'grocery', 'school'))
        self.assertTrue(clinic.feasible)
        self.assertEqual(clinic.floors, 2)
        self.assertAlmostEqual(clinic.floor_area_m2, 900, places=3)
        self.assertEqual(clinic.height_m, 8)
        self.assertTrue(grocery.feasible)
        self.assertEqual(grocery.floors, 1)
        self.assertLess(grocery.fit_score, clinic.fit_score)
        self.assertFalse(school.feasible)

    def test_large_area_in_a_narrow_strip_does_not_fit_a_grocery(self):
        self.assertFalse(design(context(width=200, length=8), 'grocery').feasible)

    def test_minimum_footprint_at_exact_coverage_limit_is_accepted(self):
        site = context(width=30, length=20)
        result = design(site, 'grocery', parcel_area=600)
        self.assertTrue(result.feasible)
        self.assertAlmostEqual(result.footprint_m2, 300, places=3)

    def test_concept_respects_holes_and_mapped_building_exclusions(self):
        parcel = box(550000, 4180000, 550080, 4180080)
        building = box(550000, 4180000, 550060, 4180045)
        hole = box(550065, 4180050, 550075, 4180065)
        free = parcel.difference(building.union(hole))
        site = context(geometry=free)
        site.existing_building_count = 1
        result = design(site, 'clinic', parcel_area=parcel.area)
        self.assertTrue(result.feasible)
        self.assertTrue(shape(site.available_geometry.model_dump()).buffer(1e-10).covers(shape(result.geometry.model_dump())))
        self.assertTrue(any('excluded' in note for note in result.notes))

    def test_tower_covering_a_lot_is_not_treated_as_open_ground(self):
        site = context(geometry=box(0, 0, 0, 0))
        site.available_geometry = None
        site.existing_building_count = 1
        self.assertFalse(design(site, 'pharmacy').feasible)


class AreaTests(unittest.TestCase):
    def test_shared_boundary_has_one_stable_assignment(self):
        tracts = [('06075010200', {}, box(0, 0, 1, 1)), ('06075010100', {}, box(1, 0, 2, 1))]
        tree = STRtree([row[2] for row in tracts])
        self.assertEqual(assign_area(tree, tracts, (1, .5)), '06075010100')
        self.assertIsNone(assign_area(tree, tracts, (3, .5)))

    def test_population_weighting_and_partial_coverage(self):
        spatial = SimpleNamespace(people=np.array([100., 300.]), supported=np.array([True, True]),
                                  baselines={c: SimpleNamespace(accessibility=np.array([0., 1.])) for c in CATEGORIES})
        results = summarize_area_needs([], spatial, ['tract', 'tract'], dict.fromkeys(CATEGORIES, 1))
        population, supported, needs = results['tract']
        self.assertEqual((population, supported), (400, 400))
        self.assertEqual(needs[0].access_deficit_pct, 25)
        spatial.supported[1] = False
        population, supported, needs = summarize_area_needs([], spatial, ['tract', 'tract'], dict.fromkeys(CATEGORIES, 1))['tract']
        self.assertEqual((population, supported), (400, 100))
        self.assertEqual(needs[0].access_deficit_pct, 100)

    def test_duplicate_geoids_and_projected_coordinates_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'tracts.geojson'
            feature = {'type':'Feature', 'properties':{'geoid':'06075010100'}, 'geometry':{'type':'Polygon','coordinates':[[[-122.4,37.7],[-122.3,37.7],[-122.3,37.8],[-122.4,37.8],[-122.4,37.7]]]}}
            path.write_text(json.dumps({'features':[feature, feature]}))
            with self.assertRaises(ValueError): load_tracts(path)
            path.write_text(json.dumps({'features':[feature], 'crs':{'properties':{'name':'EPSG:26910'}}}))
            with self.assertRaises(ValueError): load_tracts(path)


class PlanningTests(unittest.TestCase):
    def setUp(self):
        inputs = ReleaseInputs.model_validate_json((ROOT / 'contracts/fixture-inputs.json').read_bytes())
        inputs.parcels[0].properties.vacancy_status = 'source_indicated_vacant'
        self.release = Release(freeze(inputs, load_assumptions(ROOT / 'config/economics.fixture.json', 'fixture')))
        parcel = next(iter(self.release.parcels.values()))
        self.parcel_id = parcel.id
        center = parcel.properties.representative_point.coordinates
        geometry = polygon_model(box(center[0]-.01, center[1]-.01, center[0]+.01, center[1]+.01))
        needs = [AreaNeed(category_id=c, access_deficit_pct=50, covered_population=100) for c in CATEGORIES]
        area = PlanningArea(id='06075010100', geometry=geometry, properties=AreaProperties(name='Test tract', population=100,
                            supported_population=100, candidate_count=1, representative_point=center, needs=needs))
        site = SiteContext(parcel_id=parcel.id, area_id=area.id, available_geometry=parcel.geometry,
                           buildings_checked=False, existing_building_count=0)
        self.index = PlanningIndex(data_version=self.release.frozen.versions.data_version, need_targets=self.release.frozen.need_targets,
                                   sources=sources(), areas=[area], sites=[site], unassigned_population_points=0)

    def test_existing_occupied_parcel_cannot_enter_ranking(self):
        self.release.parcels[self.parcel_id].properties.vacancy_status = 'not_vacant'
        with self.assertRaises(ValueError): Planning(self.index, self.release, assumptions())

    def test_stale_index_is_rejected(self):
        self.index.data_version = 'stale'
        with self.assertRaises(ValueError): Planning(self.index, self.release, assumptions())

    def test_absolute_tract_shortage_uses_the_same_scale_and_weights(self):
        self.index.sites[0].available_geometry = context(width=100, length=100).available_geometry
        self.release.parcels[self.parcel_id].properties.area_m2 = 10000
        needs = self.index.areas[0].properties.needs
        for row in needs:
            row.access_deficit_pct = 0
        next(row for row in needs if row.category_id == 'school').access_deficit_pct = 40
        next(row for row in needs if row.category_id == 'clinic').access_deficit_pct = 10
        planning = Planning(self.index, self.release, assumptions())
        school = planning.ranking('06075010100', 'school')
        clinic = planning.ranking('06075010100', 'clinic')
        pharmacy = planning.ranking('06075010100', 'pharmacy')
        self.assertEqual(school.tract_need_priority, 1)
        self.assertAlmostEqual(school.need_weight, .85)
        self.assertAlmostEqual(clinic.tract_need_priority, 1/3)
        self.assertAlmostEqual(clinic.need_weight, .85)
        self.assertEqual(pharmacy.tract_need_priority, 0)
        self.assertAlmostEqual(pharmacy.need_weight, .85)
        for result in (school, clinic, pharmacy):
            self.assertAlmostEqual(result.need_weight + result.fit_weight + result.benefit_weight, 1)
            for site in result.sites:
                if site.score is not None:
                    raw = result.need_weight * site.need_score + result.fit_weight * site.fit_score + result.benefit_weight * site.benefit_score
                    self.assertEqual(site.score, int(raw + .5))
        # A different service's shortage cannot change the clinic's absolute score.
        next(row for row in needs if row.category_id == 'school').access_deficit_pct = 100
        same_clinic = Planning(self.index, self.release, assumptions()).ranking('06075010100', 'clinic')
        self.assertEqual(clinic.sites[0].score, same_clinic.sites[0].score)

    def test_no_need_cannot_outscore_needed_clinic_just_by_space(self):
        self.index.sites[0].available_geometry = context().available_geometry
        self.release.parcels[self.parcel_id].properties.area_m2 = 1000
        for row in self.index.areas[0].properties.needs:
            row.access_deficit_pct = 0
        next(row for row in self.index.areas[0].properties.needs if row.category_id == 'clinic').access_deficit_pct = 24.745
        planning = Planning(self.index, self.release, assumptions())
        clinic = planning.ranking('06075010100', 'clinic').sites[0]
        bank = planning.ranking('06075010100', 'bank_credit_union').sites[0]
        self.assertGreater(bank.fit_score, clinic.fit_score)
        self.assertGreater(clinic.score, bank.score)
        self.assertEqual(bank.need_level, 'No clear need')
        self.assertLessEqual(bank.score, 15)
        self.assertEqual(clinic.need_level, 'High need')

    def test_need_thresholds_are_identical_at_every_boundary(self):
        for score, label in [(0,'No clear need'),(24,'No clear need'),(25,'May help'),(49,'May help'),
                             (50,'Needed'),(74,'Needed'),(75,'High need'),(100,'High need'),(None,'Need unknown')]:
            self.assertEqual(need_level(score), label)

    def test_no_tract_population_evidence_does_not_turn_into_a_ranked_need(self):
        self.index.sites[0].available_geometry = context(width=100, length=100).available_geometry
        self.release.parcels[self.parcel_id].properties.area_m2 = 10000
        for need in self.index.areas[0].properties.needs:
            need.access_deficit_pct = None
        planning = Planning(self.index, self.release, assumptions())
        result = planning.ranking('06075010100', 'clinic')
        self.assertIsNone(result.sites[0].score)
        self.assertIsNone(result.sites[0].rank)

    def test_unavailable_need_remains_unranked_and_order_is_deterministic(self):
        self.index.sites[0].available_geometry = context(width=100, length=100).available_geometry
        # The test controls physical capacity separately from the tiny fixture lot.
        self.release.parcels[self.parcel_id].properties.area_m2 = 10000
        self.release.frozen.comparisons[self.parcel_id]['clinic'].need.deficit_reduction = None
        planning = Planning(self.index, self.release, assumptions())
        result = planning.ranking('06075010100', 'clinic')
        self.assertEqual(result.unranked_count, 1)
        self.assertIsNone(result.sites[0].rank)
        self.assertIsNone(result.sites[0].score)
        self.assertEqual(result, planning.ranking('06075010100', 'clinic'))

    def test_api_index_and_missing_index_preserve_comparison(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'planning.json'
            path.write_text(self.index.model_dump_json())
            with patch('backend.api.load_release', return_value=self.release), TestClient(create_app(mode='fixture', planning_path=path)) as client:
                result = client.get('/api/v1/planning/areas')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['features'][0]['id'], '06075010100')
                self.assertEqual(client.get('/api/v1/planning/areas/no-such-tract/sites?category=clinic').status_code, 404)
                self.assertEqual(client.get('/api/v1/planning/areas/06075010100/sites?category=office').status_code, 422)
                self.assertEqual(client.get('/api/v1/planning/areas/06075010100/sites?category=clinic&limit=101').status_code, 422)
            with TestClient(create_app(mode='fixture', planning_path=path.parent / 'missing.json')) as client:
                self.assertEqual(client.get('/api/v1/planning/areas').status_code, 503)
                self.assertEqual(client.post('/api/v1/scenarios/compare', json={'parcel_id':self.parcel_id}).status_code, 200)


if __name__ == '__main__':
    unittest.main()
