import traceback
import sys

import pylab as P
import numpy as np
import pyfits

from skymaps import DiffuseFunction,IsotropicSpectrum,IsotropicPowerLaw,IsotropicConstant
from pyLikelihood import ParameterVector, SpatialMap_cast, PointSource_cast
import pyLikelihood

from uw.like.Models import PowerLaw
from uw.like.pointspec_helpers import PointSource
from uw.like.roi_extended import ExtendedSource
from uw.like.roi_diffuse import DiffuseSource
from uw.like.Models import CompositeModel
import uw.like.Models

from SED import SED as BaseGtlikeSED

from lande.pysed import units
from lande.utilities.tools import tolist
from . tools import gtlike_or_pointlike
from . models import build_pointlike_model

_funcFactory = pyLikelihood.SourceFactory_funcFactory()

def pointlike_dict_to_spectrum(d):
    model = uw.like.Models.__dict__[d['name']]()
    for k,v in d.items(): 
        if k != 'name':
            if k[-4:] == '_err': 
                model.set_error(k[:-4],v)
            else:
                model[k]=v
    return model
    
def gtlike_dict_to_spectrum(d):
    """ Load back as a pyLikelihood spectrum object
        a spectrum that has been saved by the spectrum_to_string
        object. This undoes the conversion of spectrum_to_dict """
    spectrum=_funcFactory.create(d['name'])
    for k,v in d.items(): 
        if k not in ['name','method'] and \
           k[-4:] != '_err': spectrum.getParam(k).setTrueValue(v)
    return spectrum

def dict_to_spectrum(d):
    assert d['method'] in ['gtlike','pointlike']
    if d['method'] == 'gtlike':
        return gtlike_dict_to_spectrum(d)
    if d['method'] == 'pointlike':
        return pointlike_dict_to_spectrum(d)


def gtlike_get_full_energy_range(like): return like.energies[[0,-1]]
def pointlike_get_full_energy_range(roi): return roi.bin_edges[[0,-1]]
def get_full_energy_range(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_get_full_energy_range, pointlike_get_full_energy_range, *args, **kwargs)


def pointlike_spectrum_to_dict(model, errors=False):
    """ Package of a spectral model into a handy
        python dictionary.

            >>> m=PowerLaw(norm=1, index=-.5)
            >>> d=spectrum_to_dict(m)
            >>> print d['Norm']
            1.0
            >>> print d['Index']
            -0.5
            >>> d=spectrum_to_dict(m, errors=False)
            >>> d.has_key('Index_err')
            False

        Note, the way to save a ComositeModel is a little different.

            >>> from uw.like.Models import SumModel,LogParabola
            >>> pl=PowerLaw()
            >>> lp=LogParabola()
            >>> c = SumModel(pl,lp)
            >>> s=spectrum_to_dict(c)
            >>> s.keys()
            ['models', 'name']
            >>> len(s['models'])
            2
            >>> s['models'][0] == spectrum_to_dict(pl)
            True
            >>> s['models'][1] == spectrum_to_dict(lp)
            True
    """
    d = dict(name = model.name, method='pointlike')
    if isinstance(model,CompositeModel):
        d['models'] = map(pointlike_spectrum_to_dict,model.models)
        return d
    else:
        for p in model.param_names:
            d[p] = model[p]
            if errors:
                d['%s_err' % p] = model.error(p)
        for p in model.default_extra_params.keys():
            d[p] = getattr(model,p)

        return tolist(d)

def gtlike_spectrum_to_dict(spectrum, errors=False):
    """ Convert a pyLikelihood object to a python 
        dictionary which can be easily saved to a file. """
    parameters=ParameterVector()
    spectrum.getParams(parameters)
    d = dict(name = spectrum.genericName(), method='gtlike')
    for p in parameters: 
        d[p.getName()]= p.getTrueValue()
        if errors: 
            d['%s_err' % p.getName()]= p.error()*p.getScale() if p.isFree() else np.nan
    return d


def gtlike_name_to_spectral_dict(like, name, errors=False, minos_errors=False):
    source = like.logLike.getSource(name)
    spectrum = source.spectrum()
    d=gtlike_spectrum_to_dict(spectrum, errors)
    if minos_errors:
        parameters=ParameterVector()
        spectrum.getParams(parameters)
        for p in parameters: 
            pname = p.getName()
            if p.isFree():
                lower,upper=like.minosError(name, pname)
                d['%s_lower_err' % pname] = -1*lower*p.getScale()
                d['%s_upper_err' % pname] = upper*p.getScale()
            else:
                d['%s_lower_err' % pname] = np.nan
                d['%s_upper_err' % pname] = np.nan
    return d

def pointlike_name_to_spectral_dict(roi, name, *args, **kwargs):
    model = roi.get_model(name)
    return pointlike_spectrum_to_dict(model, *args, **kwargs)


def gtlike_fluxdict(like,name, emin=None,emax=None,flux_units='erg', errors=True, include_prefactor=False, prefactor_energy=None):

    if emin is None and emax is None: 
        emin, emax = get_full_energy_range(like)

    ce=lambda e: units.convert(e,'MeV',flux_units)
    f=dict(flux=like.flux(name,emin=emin,emax=emax),
           flux_units='ph/cm^2/s',
           eflux=ce(like.energyFlux(name,emin=emin,emax=emax)),
           eflux_units='%s/cm^2/s' % flux_units,
           emin=emin,
           emax=emax)

    if errors:
        try:
            # incase the errors were not calculated
            f['flux_err']=like.fluxError(name,emin=emin,emax=emax)
            f['eflux_err']=ce(like.energyFluxError(name,emin=emin,emax=emax))
        except Exception, ex:
            print 'ERROR calculating flux error: ', ex
            traceback.print_exc(file=sys.stdout)
            f['flux_err']=-1
            f['eflux_err']=-1

    if include_prefactor:
        assert prefactor_energy is not None
        source = like.logLike.getSource(name)
        spectrum = source.spectrum()
        cp = lambda e: units.convert(e,'1/MeV','1/%s' % flux_units)
        f['prefactor'] = cp(BaseGtlikeSED.get_dnde(spectrum,prefactor_energy))
        f['prefactor_units'] = 'ph/cm^2/s/%s' % flux_units
        f['prefactor_energy'] = prefactor_energy
        f['prefactor_energy_units'] = 'MeV'
    return tolist(f)

def gtlike_powerlaw_prefactor_dict(like, name, flux_units='erg', errors=True, minos_errors=False):
    cp = lambda e: units.convert(e,'1/MeV','1/%s' % flux_units)

    source = like.logLike.getSource(name)
    spectrum = source.spectrum()
    assert spectrum.genericName() == 'PowerLaw'
    pref = spectrum.getParam('Prefactor')
    scale = spectrum.getParam('Scale')

    d=dict()
    d['prefactor'] = cp(pref.getTrueValue())
    if errors:
        d['prefactor_err'] = cp(pref.error()*pref.getScale())
    if minos_errors:
        lower,upper=like.minosError(name, 'Prefactor')
        d['prefactor_lower_err'] = cp(-1*lower*pref.getScale())
        d['prefactor_upper_err'] = cp(upper*pref.getScale())

    d['prefactor_units'] = 'ph/cm^2/s/%s' % flux_units
    d['prefactor_energy'] = scale.getTrueValue()
    d['prefactor_energy_units'] = 'MeV'
    return d


def gtlike_get_spatial_model_name(like, name):
    """ This code is adapted from
        the Likelihood file SourceModelBuilder.cxx's
        function
        SourceModelBuilder::addSpatialPart
    """
    source = like.logLike.getSource(name)
    fns = source.getSrcFuncs()

    assert fns.count("Position") or fns.count("SpatialDist")

    if fns.count("Position"):
        return "SkyDirFunction"

    elif fns.count("SpatialDist"):
        type = fns["SpatialDist"].genericName()
        return type

def pointlike_get_spatial_model_name(roi, name):
    source = roi.get_source(name)
    if isinstance(source,PointSource):
        return 'SkyDirFunction'
    elif isinstance(source,ExtendedSource):
        # this is only approximately true
        return 'SpatialMap'
    elif isinstance(source,DiffuseSource):
        dm = source.dmodel
        if hasattr(dm,'__len__') and len(dm)==1: dm = dm[0]
        if isinstance(dm,DiffuseFunction):
            return 'MapCubeFunction'
        elif isinstance(dm,IsotropicSpectrum) or \
                isinstance(dm,IsotropicPowerLaw) or \
                isinstance(dm,IsotropicConstant):
            return 'ConstantValue'
    else:
        raise Exception("Unknown spatial model for source %s" % source.name)

def get_spatial_model_name(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_get_spatial_model_name, pointlike_get_spatial_model_name, *args, **kwargs)

def pointlike_get_all_names(roi):
    """ Get a list of the names of all sources in the pointlike ROI. """
    return np.append(roi.psm.names,roi.dsm.names)

def gtlike_get_all_names(like):
    """ Get a list of the names of all sources in the gtlike ROI. """
    return like.sourceNames()

def get_all_names(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_get_all_names, pointlike_get_all_names, *args, **kwargs)


def get_sources(like_or_roi):
    """ Get a list of point-like and extended sources
        in the ROI. """
    all_names=get_all_names(like_or_roi)
    all_ps = [i for i in all_names \
              if get_spatial_model_name(like_or_roi,i) in \
              ['SkyDirFunction','SpatialMap']]
    return all_ps
        

def get_background(like_or_roi):
    """ Get a list of the names of all background
        sources in the ROI. """
    all_names=get_all_names(like_or_roi)
    all_bg = [i for i in all_names \
              if get_spatial_model_name(like_or_roi,i) in \
              ['ConstantValue','MapCubeFunction']]
    return all_bg

def diffusedict(like_or_roi):
    """ Save out all diffuse sources. """

    f = dict()
    bgs = get_background(like_or_roi)
    for name in bgs:
        f[name] = name_to_spectral_dict(like_or_roi, name, errors=True)
    return tolist(f)

def gtlike_ts_dict(like, name):
    return dict(
        reoptimize=like.Ts(name,reoptimize=True, verbosity=4),
        noreoptimize=like.Ts(name,reoptimize=False, verbosity=4)
        )

def gtlike_sourcedict(like, name, emin=None, emax=None, 
                      flux_units='erg', errors=True, minos_errors=True, 
                      save_TS=True, add_diffusedict=True):

    if emin is None and emax is None:
        emin, emax = get_full_energy_range(like)

    d=dict(
        logLikelihood=logLikelihood(like),
    )
    
    d['model']= name_to_spectral_dict(like, name, errors=errors, minos_errors=minos_errors)

    if save_TS:
        d['TS']=gtlike_ts_dict(like, name)

    d['flux']=fluxdict(like,name,emin,emax,flux_units=flux_units, errors=errors)


    if add_diffusedict:
        d['diffuse'] = diffusedict(like)

    return tolist(d)

def sourcedict(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_sourcedict, pointlike_sourcedict, *args, **kwargs)

def pointlike_model_to_flux(model, emin, emax, flux_units='erg', errors=True, include_prefactor=False, prefactor_energy=None):

    ce=lambda e: units.convert(e,'MeV',flux_units)
    f=dict()
    if errors:
        f['flux'],f['flux_err']=model.i_flux(emin=emin,emax=emax,error=True)
        ef,ef_err=model.i_flux(emin=emin,emax=emax,e_weight=1,error=True)
        f['eflux'],f['eflux_err']=ce(ef),ce(ef_err)
    else:
        f['flux']=model.i_flux(emin=emin,emax=emax,error=False)
        ef=model.i_flux(emin=emin,emax=emax,e_weight=1,error=False)
        f['eflux']=ce(ef)

    f['flux_units']='ph/cm^2/s'
    f['eflux_units']='%s/cm^2/s' % flux_units
    f['emin'],f['emax']=emin,emax

    if include_prefactor:
        assert prefactor_energy is not None
        cp = lambda e: units.convert(e,'1/MeV','1/%s' % flux_units)
        f['prefactor'] = cp(model(prefactor_energy))
        f['prefactor_units'] = 'ph/cm^2/s/%s' % flux_units
        f['prefactor_energy'] = prefactor_energy
        f['prefactor_energy_units'] = 'MeV'

    return tolist(f)

def pointlike_fluxdict(roi, which, emin=None, emax=None, *args, **kwargs):

    if emin is None and emax is None:
        emin, emax = get_full_energy_range(roi)

    model=roi.get_model(which)
    return tolist(pointlike_model_to_flux(model, emin, emax, *args, **kwargs))

def pointlike_powerlaw_prefactor_dict(roi, which, flux_units='erg', errors=True):
    model=roi.get_model(which)

    assert isinstance(model,PowerLaw)

    cp = lambda e: units.convert(e,'1/MeV','1/%s' % flux_units)
    d = dict()
    d['prefactor'] = cp(model['norm'])
    if errors:
        d['prefactor_err'] = cp(model.error('norm'))
    d['prefactor_units'] = 'ph/cm^2/s/%s' % flux_units
    d['prefactor_energy'] = model.e0
    d['prefactor_energy_units'] = 'MeV'
    return d


def energy_dict(emin, emax, energy_units='MeV'):
    ce=lambda e: units.convert(e,'MeV',energy_units)
    return dict(emin=ce(emin),
                emax=ce(emax),
                emiddle=ce(np.sqrt(emin*emax)),
                energy_units=energy_units)


def skydirdict(skydir):
    return tolist(dict(
        gal = [skydir.l(),skydir.b()],
        equ = [skydir.ra(),skydir.dec()]))

def pointlike_ts_dict(roi, name):
    return roi.TS(name,quick=False)

def pointlike_sourcedict(roi, name, emin=None, emax=None, flux_units='erg', errors=True, save_TS=True, add_diffusedict=True):
    d={}

    if emin is None and emax is None:
        emin, emax = get_full_energy_range(roi)

    old_quiet = roi.quiet; roi.quiet=True
    if save_TS:
        d['TS']=pointlike_ts_dict(roi,name)

    roi.quiet = old_quiet

    d['logLikelihood']=logLikelihood(roi)

    d['flux']=fluxdict(roi,name,emin,emax,flux_units, errors=errors)

    d['model']= name_to_spectral_dict(roi, name)

    # Source position
    d['position'] = skydirdict(source.skydir)

    if diffusedict:
        d['diffuse'] = add_diffusedict(roi)

    d['spatial_model'] = spatial_dict(source, roi)

    return tolist(d)

def spatial_dict(source, roi):
    f = dict()
    if isinstance(source,ExtendedSource):
        # Extended Source parameters
        spatial_model = source.spatial_model
        for param in spatial_model.param_names:
            f[param]=spatial_model[param]
            f[param + '_err']=spatial_model.error(param)
        f['r68'] = spatial_model.r68()
        f['r99'] = spatial_model.r99()

    # add elliptical error, if they exist.
    # N.B. If no localization performed, this will return
    # an empty dictionary.
    # N.B. This method will do the wrong thing if you have recently relocalized
    # another source. This is rarely the case.
    f['ellipse'] = roi.get_ellipse()
    return tolist(f)

def ts_dict(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_ts_dict, pointlike_ts_dict, *args, **kwargs)

def spectrum_to_dict(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_spectrum_to_dict, pointlike_spectrum_to_dict, *args, **kwargs)

def name_to_spectral_dict(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_name_to_spectral_dict, pointlike_name_to_spectral_dict, *args, **kwargs)

def fluxdict(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_fluxdict, pointlike_fluxdict, *args, **kwargs)

def powerlaw_prefactor_dict(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_powerlaw_prefactor_dict, pointlike_powerlaw_prefactor_dict, *args, **kwargs)

def pointlike_logLikelihood(roi): return -roi.logLikelihood(roi.parameters())

def gtlike_logLikelihood(like): return like.logLike.value()

def logLikelihood(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_logLikelihood, pointlike_logLikelihood, *args, **kwargs)

def gtlike_get_roi_dir(like):
    dir=like.binnedData.countsMap.refDir()
    return dir
def pointlike_get_roi_dir(roi):
    return roi.roi_dir

def get_roi_dir(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_get_roi_dir, pointlike_get_roi_dir, *args, **kwargs)


def gtlike_get_skydir(like, name):
    """ Get the skydir for a gtlike point or extended source. """
    if not name in get_sources(like):
        raise Exception("Unable to get skydir because %s is not a point or extended source." % name)

    spatial_model = get_spatial_model_name(like, name)
    assert spatial_model in ['SkyDirFunction', 'SpatialMap']

    source = like.logLike.getSource(name)

    if spatial_model == 'SkyDirFunction':
        return PointSource_cast(source).getDir()

    elif spatial_model == 'SpatialMap':
        spatial_map=SpatialMap_cast(source.getSrcFuncs()['SpatialDist'])
        
        # This is kind of ugly, but I can't find out how to get m_refDir out of WcsMap2 object
        filename = spatial_map.fitsFile()
        pf=pyfits.open(filename)
        h=pf['PRIMARY'].header
        crpix1=h['CRPIX1']
        crpix2=h['CRPIX2']
        return spatial_map.wcsmap().skyDir(crpix1,crpix2)

def pointlike_get_skydir(roi, name):
    source = roi.get_source(name)
    if not isinstance(source,PointSource) and not isinstance(source,ExtendedSource):
        raise Exception("Unable to get skydir because %s is not a point or extended source." % name)
    return source.skydir

def get_skydir(*args, **kwargs):
    return gtlike_or_pointlike(gtlike_get_skydir, pointlike_get_skydir, *args, **kwargs)

if __name__ == "__main__":
    import doctest
    doctest.testmod()
