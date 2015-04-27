# -*- coding: utf-8 -*-
import random
from collections import namedtuple

import numpy as np

from grass.pygrass.modules.shortcuts import raster as r
from grass.pygrass.gis.region import Region
from grass.pygrass.vector import VectorTopo
from grass.pygrass.vector.geometry import Line
from grass.pygrass.vector.table import Link


COLS = [(u'cat',       'INTEGER PRIMARY KEY'),
        (u'plant_id',  'VARCHAR(10)'),
        (u'stream_id',  'INTEGER'),
        (u'potential_power', 'DOUBLE')]


COLS_points = [(u'cat',       'INTEGER PRIMARY KEY'),
               (u'kind',  'VARCHAR(10)'),
               (u'plant_id',  'VARCHAR(10)'),
               (u'kind_label',  'VARCHAR(10)'),
               (u'stream_id',  'INTEGER'),
               (u'elevation', 'DOUBLE'),
               (u'discharge', 'DOUBLE'),
               (u'potential_power', 'DOUBLE')]

HydroStruct = namedtuple('HydroStruct',
                         ['intake', 'conduct', 'penstock', 'side'])


def isinverted(line, elev, region):
    el0, el1 = elev.get_value(line[0]), elev.get_value(line[-1])
    return False if el0 > el1 else True


def not_overlaped(line):
    """The countur lines are always a ring even if we see tham as line
    they are just overlaped, we want to avoid this situation
    therefore we return only the part of the line that is not overlaped
    """
    if len(line) >= 2 and line[1] == line[-2]:
        return Line(line[:len(line)//2+1])
    return line


def sort_by_elev(line, elev, region):
    """Return a Line object sorted using the elevation, the first point will
    be the higher and the last the lower.
    """
    if isinverted(line, elev, region):
        line.reverse()
    return line


def sort_by_west2east(line):
    """Return a Line object sorted using east coordinate, the first point will
    be the wester and the last the easter.
    """
    if line[0].x > line[-1].x:
        line.reverse()
    return line


def splitline(line, point):
    """Split a line using a point. return two lines that start with the point.

                  point
                     *---------
                      \        \
                       \        -------->
                        \
                         --------|

    """
    dist = line.distance(point)
    l0 = line.segment(0, dist.sldist)
    l0.reverse()
    l1 = line.segment(dist.sldist, line.length())
    return l0, l1



def read_plants(hydro, elev=None, restitution='restitution', intake='intake',
                cid_plant='id_plant', cid_point='id_point', ckind_label='kind_label',
                celevation='elevation', cdischarge='discharge'):
    plants = {}
    skipped = []
    for pnt in hydro:
        if pnt is None:
            import ipdb; ipdb.set_trace()
        if elev is None:
            select = ','.join([cid_plant, cid_point, ckind_label, celevation, cdischarge])
            id_plant, id_point, kind_label, el, disch = pnt.attrs[select]
        else:
            select = ','.join([cid_plant, cid_point, ckind_label, cdischarge])
            id_plant, id_point, kind_label, disch = pnt.attrs[select]
            el = elev.get_value(pnt)
        plant = plants.get(id_plant, Plant(id_plant))
        if kind_label == restitution:
            plant.restitution = Restitution(id_point, pnt, el)
        elif kind_label == intake:
            plant.intakes.add(Intake(id_point=id_point, point=pnt,
                                     elevation=el, id_plants=id_plant,
                                     discharge=disch if disch else 1.))
        else:
            skipped.append((id_plant, id_point, kind_label))
        plants[id_plant] = plant
    return plants, skipped


def write_plants(plants, output, stream, elev, overwrite=False):
    """Write a vector map with the plant"""
    with VectorTopo(output, mode='w', tab_cols=COLS,
                    overwrite=overwrite) as out:
        for p in plants:
            potential_power= plants[p].potential_power()
            plant_id = plants[p].id
            lines, ids = plants[p].plant(stream, elev)
            for line, r_id in zip(lines, ids):
                out.write(line, (plant_id, r_id, potential_power, ))
        out.table.conn.commit()


def write_structures(plants, output, elev, stream=None, overwrite=False):
    """Write a vector map with the plant structures"""
    def write_hydrostruct(out, hydro, plant):
        #import pdb; pdb.set_trace()
        pot = plant.potential_power(intakes=[hydro.intake, ])
        (plant_id, itk_id, side,
         disch, gross_head) = (plant.id, hydro.intake.id, hydro.side,
                         float(hydro.intake.discharge),
                         float(hydro.intake.elevation -
                               plant.restitution.elevation))
        out.write(hydro.conduct,
                  (plant_id, itk_id, disch, 0., 0., 'conduct', side))
        out.write(hydro.penstock,
                  (plant_id, itk_id, disch, gross_head, pot, 'penstock', side))
        out.table.conn.commit()

    tab_cols = [(u'cat',        'INTEGER PRIMARY KEY'),
                (u'plant_id',   'VARCHAR(10)'),
                (u'intake_id',  'INTEGER'),
                (u'discharge',  'DOUBLE'),
                (u'gross_head', 'DOUBLE'),
                (u'power',      'DOUBLE'),
                (u'kind',       'VARCHAR(10)'),
                (u'side',       'VARCHAR(10)'), ]
    with VectorTopo(output, mode='w', overwrite=overwrite) as out:
        link = Link(layer=1, name=output, table=output,
                    driver='sqlite')
        out.open('w')
        out.dblinks.add(link)
        out.table = out.dblinks[0].table()
        out.table.create(tab_cols)
        print('Number of plants: %d' % len(plants))
        for p in plants:
            plant = plants[p]
            print(plant.id)
            #import ipdb; ipdb.set_trace
            for options in plant.structures(elev, stream=stream):
                for hydro in options:
                    print('writing: ', hydro.intake)
                    write_hydrostruct(out, hydro, plant)


class AbstractPoint(object):
    def __init__(self, id_point, point, elevation, id_plants=None,
                 id_stream=None, discharge=1.):
        self.id = id_point
        self.point = point
        self.elevation = elevation
        self.id_plants = id_plants
        self.id_stream = id_stream
        self.discharge = discharge

    def __repr__(self):
        srepr = "%s(id_point=%r, point=%r, elevation=%r)"
        return srepr % (self.__class__.__name__, self.id,
                        self.point, self.elevation)


class Restitution(AbstractPoint):
    pass


class Intake(AbstractPoint):
    pass


class Plant(object):
    def __init__(self, id_plant, id_stream=None, restitution=None,
                 intakes=None, line=None):
        self.id = id_plant
        self.id_stream = id_stream
        self.restitution = restitution
        self.intakes = set() if intakes is None else set(intakes)
        self.line = line

    def __repr__(self):
        srepr = "%s(id_plant=%r, restitution=%r, intakes=%r)"
        return srepr % (self.__class__.__name__, self.id,
                        self.restitution, self.intakes)

    def potential_power(self, efficiency=1., intakes=None):
        """Return the potential of the plant: input discharge [m3/s],
        elevetion [m] and output [kW].

        Parameters
        ----------

        efficiency: float
            It is a float value between 0 and 1 to take into account
            the efficiency of the turbine, default value is 1.
        intakes: iterable of Intake instances
            It is an iterable object to specify a subset of the intakes
            that will be used to compute the potential.

        Returns
        -------

        potential: float
            It is the potential of the plant without the flow.
        """
        intakes = self.intakes if intakes is None else intakes
        # create an array with all the elevation of the intakes points
        # and the discharge
        elev = np.array([ink.elevation for ink in intakes])
        discharge = np.array([ink.discharge for ink in intakes])
        return ((elev - self.restitution.elevation)
                * discharge * 9.810 * efficiency).sum()

    def plant(self, stream, elev, maxdist=1.0, region=None):
        """Return a list with the segments involved by the plant.

        Parameters
        ----------

        strem: vector map
            A vector map instance that is already opened containing lines with
            the streams.
        maxdist: float
            It is the maximum distance that will be used to find the closest
            point, if no line is found for the point a TypeError is raised.
        elev: raster map
            A rester map instance that it is already opened with the digital
            elevation values of the study area, if not given the function
            will perform a network serach to find the path that link the
            intake and restoration points.

        Returns
        -------

        lines: a list of lines
            A dictionary of vector features ids and tuple with the segments. ::

                {id1: ([Line(...), ], [Line(...), ]), # plant intake
                 id2: (Line(...), []),                # whole part of the plant
                 ...
                 idN: (Line(...), [Line(...), ]), }   # plant restitution

            An example could be an intake (x) and restitution (0) that are
            on the same line. ::

                 <---------------------id--------------------->
                |============x=================o===============|
                |<---iseg--->|
                |<------------rseg------------>|
                |-----a------|--------b--------|--------c------|

            the list returned is: [b, ]

            For an intake (x) and restitution (o) that are on different lines

                 <------id1----->  <------id2---->  <---id3---->
                |======x=========||===============||========o===|
                |<-i0->|
                                                   |<--r0-->|
                |---a--|-----b---|--------c--------|----d---|-e-|

            the list returned is: [b, c, d]

        """
        def error(pnt):
            raise TypeError("Line not found for %r" % pnt)

        def split_line(pnt):
            """Return a tuple with three elements:
            0. the line
            1. the distance from the beginning of the line
            2. the total length of the line.
            """
            line = stream.find['by_point'].geo(pnt, maxdist=maxdist)
            if line is None:
                error(pnt)
            (newpnt, _, _, seg) = line.distance(pnt)
            return line, seg, line.length()

        def find_path(line, res):
            def lower(line):
                """Return the lower node of the line"""
                nfirst, nlast = line.nodes()
                first = elev.get_value(nfirst, region=region)
                last = elev.get_value(nlast, region=region)
                return nlast if first > last else nfirst

            def next_line(node, prev, resid):
                """Return a generator with the lines until the restitution."""
                for line in node.lines():
                    if line.id != prev.id:
                        nd = lower(line)
                        if nd.id != node.id:
                            if line.id != resid:
                                yield line
                                for l in next_line(nd, line, resid):
                                    yield l
                            else:
                                yield line

            lines = []
            node = lower(line)
            lines = [l for l in next_line(node, line, res.id)]
            return lines

        lines = []
        ids = []
        reg = Region() if region is None else region
        #stream, mset = stream.split('q') if '@' in stream else (stream, '')
        #with VectorTopo(stream, mapset=mset, mode='r') as stm:
        res, rseg, rlen = split_line(self.restitution.point)
        for intake in self.intakes:
            itk, iseg, ilen = split_line(intake.point)
            if itk.id == res.id:
                start, end = (rseg, iseg) if rseg <= iseg else (iseg, rseg)
                line = res.segment(start, end)
                lines.append(sort_by_elev(line, elev, reg))
                ids.append(itk.id)
            else:
                if isinverted(itk, elev, reg):
                    iseg = ilen - iseg
                    itk.reverse()
                line = itk.segment(iseg, ilen)
                lines.append(line)
                ids.append(itk.id)
                for line in find_path(itk, res):
                    l_id = line.id
                    if l_id == res.id:
                        if isinverted(line, elev, reg):
                            rseg = rlen - rseg
                            line.reverse()
                        line = line.segment(0, rseg)
                    lines.append(sort_by_elev(line, elev, reg))
                    ids.append(l_id)
        return lines, ids

    def structures(self, elev, stream=None):
        """Return a tuple with lines structres options of a hypotetical plant.

        ::

              river
               \ \
                \i\-------_______
                |\ \              \
                | \ \              \
                )  \ \              )  cond0
               /    \ \           /
              /      \ \         /
             ( cond1  \ \       /
              \        \ \      |
               \        \ \     |
                \        \ \    |
                 o--------\r\---o
               pstk1       \ \   pstk0
                            \ \

        Parameters
        ----------

        elev: raster
            Raster instance already opened with the elevation.
        intake_pnt: point
            It is the point of the intake.
        restitution_pnt: point
            It is the point of the restitution.

        Returns
        -------

        a list of tuple: [(HydroStruct(intake, conduct=cond0, penstock=pstk0),
                           HydroStruct(intake, conduct=cond1, penstock=pstk1))]
           Return a list of tuples, containing two HydroStruct the first with
           the shortest penstock and the second with the other option.
        """
        def get_struct(contur, respoint):
            """Return the lines of the conduct and the penstock.

            Parameters
            ----------

            contur: line segment
                It is a line segment of the contur line splited with splitline
                function, where the first point is the intake.
            respoint: point
                It is the point of the plant restitution.

            Returns
            -------

            tuple: (conduct, penstock)
               Return two lines, the first with the conduct and the second with
               the penstock. Note: both conduct and penstock lines are coherent
               with water flow direction.
            """
            dist = contur.distance(respoint)
            conduct = contur.segment(0, dist.sldist)
            penstock = Line([dist.point, respoint])
            return conduct, penstock

        def get_all_structs(contur, itk, res):
            l0, l1 = splitline(contur, itk.point)
            # get structs
            c0, p0 = get_struct(l0, res.point)
            c1, p1 = get_struct(l1, res.point)
            s0, s1 = 'option0', 'option1'
            # TODO: uncomment this to have left and right distinction...
            # but sofar is not working properly, therefore is commented.
            #if stream is not None:
            #    sitk = stream.find['by_point'].geo(itk.point, maxdist=100000)
            #    s0, s1 = (('right', 'left') if isinverted(sitk, elev, reg)
            #              else ('left', 'right'))
            return (HydroStruct(itk, c0, p0, s0), HydroStruct(itk, c1, p1, s1))

        result = []
        reg = Region()
        for itk in self.intakes:
            tmpvect = 'tmpvect%d' % random.randint(1000, 9999)
            # generate the contur line that pass to the point
            # TODO: maybe we can compute the contour with all the levels taked
            # from the all te intake, just to compute this once
            r.contour(input='%s@%s' % (elev.name, elev.mapset),
                      output=tmpvect, step=0,  levels=itk.elevation,
                      overwrite=True)
            with VectorTopo(tmpvect, mode='r') as cnt:
                # find the closest contur line
                contur_res = cnt.find['by_point'].geo(self.restitution.point,
                                                      maxdist=100000.0)
                contur = not_overlaped(contur_res)
                contur = sort_by_west2east(contur)
                # TODO: probably find the contur line for the intake and
                # the restitution it is not necessary, and we could also remove
                # the check bellow: contur_itk.id != contur_res.id
                contur_itk = cnt.find['by_point'].geo(itk.point,
                                                      maxdist=100000.0)
                if contur_itk is None or contur_res is None:
                    msg = ('Not able to find the contur line closest to the '
                           'intake point %r, of the plant %r'
                           'from the contur line map: %s')
                    raise TypeError(msg % (itk, self, cnt.name))
                if contur_itk.id != contur_res.id:
                    print('=' * 30)
                    print(itk)
                    msg = "Contur lines are different! %d != %d, in %s"
                    print(msg % (contur_itk.id, contur_res.id, cnt.name))
                result.append(get_all_structs(contur, itk, self.restitution))
            # remove temporary vector map
            cnt.remove()
        return result