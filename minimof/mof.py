import numpy as np
from ngmix.gmix import GMix
from ngmix.fitting import LMSimple
from ngmix.fitting import run_leastsq
from ngmix.gmix import (
    get_model_num,
    get_model_name,
    get_model_ngauss,
    get_model_npars,
)

# weaker than usual
_default_lm_pars={
    'maxfev':2000,
    'ftol': 1.0e-3,
    'xtol': 1.0e-3,
}


class MOF(LMSimple):
    """
    fit multiple objects simultaneously
    """
    def __init__(self, obs, model, nobj, **keys):
        """
        currently model is same for all objects
        """
        super(LMSimple,self).__init__(obs, model, **keys)

        assert self.prior is not None,"send a prior"
        self.nobj=nobj

        # center1 + center2 + shape + T + fluxes for each object
        self.n_prior_pars=self.nobj*(1 + 1 + 1 + 1 + self.nband)
        self._set_fdiff_size()

        self.npars_per = 5+self.nband
        self.npars = self.nobj*self.npars_per

        self._band_pars=np.zeros(6*self.nobj)

        self.lm_pars={}
        self.lm_pars.update(_default_lm_pars)

        lm_pars=keys.get('lm_pars',None)
        if lm_pars is not None:
            self.lm_pars.update(lm_pars)


    def go(self, guess):
        """
        Run leastsq and set the result
        """

        guess=np.array(guess,dtype='f8',copy=False)

        # assume 5+nband pars per object
        nobj = guess.size//self.npars_per
        nleft = guess.size % self.npars_per
        if nobj != self.nobj or nleft != 0:
            raise ValueError("bad guess size: %d" % guess.size)

        self._setup_data(guess)

        self._make_lists()

        result = run_leastsq(
            self._calc_fdiff,
            guess,
            self.n_prior_pars,
            **self.lm_pars
        )

        result['model'] = self.model_name
        if result['flags']==0:
            result['g'] = result['pars'][2:2+2].copy()
            result['g_cov'] = result['pars_cov'][2:2+2, 2:2+2].copy()
            stat_dict=self.get_fit_stats(result['pars'])
            result.update(stat_dict)

        self._result=result

    def get_band_pars(self, pars_in, band):
        """
        Get linear pars for the specified band
        """

        pars=self._band_pars

        for i in range(self.nobj):
            # copy cen1,cen2,g1,g2,T
            beg=i*6
            end=beg+5

            ibeg = i*self.npars_per
            iend = ibeg+5

            pars[beg:end] = pars_in[ibeg:iend]

            # now copy the flux
            pars[beg+5] = pars_in[ibeg+5+band]

        return pars

    def _make_model(self, band_pars):
        """
        generate a gaussian mixture with the right number of
        components
        """
        return GMixModelMulti(band_pars, self.model)

    def get_gmix(self, band=0):
        """
        Get a gaussian mixture at the fit parameter set, which
        definition depends on the sub-class

        parameters
        ----------
        band: int, optional
            Band index, default 0
        """
        res=self.get_result()
        self._fill_gmix_all_nopsf(res['pars'])
        return self._gmix_all[band][0]

    def get_convolved_gmix(self, band=0, obsnum=0):
        """
        get a gaussian mixture at the fit parameters, convolved by the psf if
        fitting a pre-convolved model

        parameters
        ----------
        band: int, optional
            Band index, default 0
        obsnum: int, optional
            Number of observation for the given band,
            default 0
        """
        res=self.get_result()
        self._fill_gmix_all(res['pars'])
        return self._gmix_all[band][obsnum]

    def make_image(self, band=0, obsnum=0):
        gm = self.get_convolved_gmix(band=band, obsnum=obsnum)
        obs = self.obs[band][obsnum]
        return gm.make_image(obs.image.shape, jacobian=obs.jacobian)

# move to ngmix
class GMixModelMulti(GMix):
    """
    A two-dimensional gaussian mixture created from a set of model parameters
    for multiple objects

    Inherits from the more general GMix class, and all its methods.

    parameters
    ----------
    pars: array-like
        Parameter array. The number of elements will depend
        on the model type, the total number being nobj*npars_model
    model: string or gmix type
        e.g. 'exp' or GMIX_EXP
    """
    def __init__(self, pars, model):

        self._model      = get_model_num(model)
        self._model_name = get_model_name(model)

        self._ngauss_per = get_model_ngauss(self._model)
        self._npars_per  = get_model_npars(self._model)

        np = len(pars)
        self._nobj = np//self._npars_per
        if (np % self._npars_per) != 0:
            raise ValueError("bad number of pars: %s" % np)

        self._npars = self._nobj*self._npars_per
        self._ngauss = self._nobj*self._ngauss_per

        self.reset()

        self._set_fill_func()
        self.fill(pars)

    def get_nobj(self):
        """
        number of objects represented
        """
        return self._nobj

    def copy(self):
        """
        Get a new GMix with the same parameters
        """
        gmix = GMixModelMulti(self._pars, self._model_name)
        return gmix

    def set_cen(self, row, col):
        """
        Move the mixture to a new center

        set pars as well
        """
        raise NotImplementedError("would only make sense if multiple "
                                  "rows and cols sent")

    def _fill(self, pars):
        """
        Fill in the gaussian mixture with new parameters, without
        error checking

        parameters
        ----------
        pars: ndarray or sequence
            The parameters
        """

        self._pars[:] = pars

        gmall=self.get_data()

        ng=self._ngauss_per
        np=self._npars_per
        for i in range(self._nobj):
            beg=i*ng
            end=(i+1)*ng

            pbeg=i*np
            pend=(i+1)*np

            # should be a reference
            gm = gmall[beg:end]
            gpars = pars[pbeg:pend]

            self._fill_func(
                gm,
                gpars,
            )

    def make_image(self, band, fast_exp=False):
        """
        render the full model
        """
        obs=self.obs[band][0]

        res=self.get_result()
        if res['flags'] != 0:
            raise RuntimeError("can't render a failure")
        dims=obs.image.shape

        image=numpy.zeros(dims, dtype='f8')

        coords=make_coords(image.shape, obs.jacobian)

        gmall=self.get_data()

        ng=self._ngauss_per
        for i in range(self._nobj):
            beg=i*ng
            end=(i+1)*ng

            # should be a reference
            gm = gmall[beg:end]

            ngmix.render_nb.render(
                gm,
                coords,
                image,
                fast_exp=fast_exp,
            )

        return image


