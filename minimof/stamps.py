"""
TODO:
    plot position in stamp
    plot objects with residuals
    fit statistics
        s/n, chi2/dof, etc.
        probably easiest to calculate s/n of model, roundified
    explore box sizes
        maybe larger
    explore threshold
        how many spurious?  maybe test with nothing images

"""
from __future__ import print_function
import numpy as np
import esutil as eu
import meds
import ngmix

from . import mof

class MEDSInterface(meds.MEDS):
    def __init__(self, image, weight, seg, bmask, cat):
        self._image_data=dict(
            image=image,
            weight=weight,
            seg=seg,
            bmask=bmask,
        )
        self._cat=cat

    def get_cutout(self, iobj, icutout, type='image'):
        """
        Get a single cutout for the indicated entry

        parameters
        ----------
        iobj:
            Index of the object
        icutout:
            Index of the cutout for this object.
        type: string, optional
            Cutout type. Default is 'image'.  Allowed
            values are 'image','weight','seg','bmask'

        returns
        -------
        The cutout image
        """

        if type=='psf':
            return self.get_psf(iobj,icutout)

        self._check_indices(iobj, icutout=icutout)

        if type not in self._image_data:
            raise ValueError("bad cutout type: '%s'" % type)

        im=self._image_data[type]

        c=self._cat
        srow=c['orig_start_row'][iobj,icutout]
        scol=c['orig_start_col'][iobj,icutout]
        erow=c['orig_end_row'][iobj,icutout]
        ecol=c['orig_end_col'][iobj,icutout]

        return im[
            srow:erow,
            scol:ecol,
        ].copy()

    def get_obslist(self, iobj, weight_type='weight'):
        """
        get an ngmix ObsList for all observations

        parameters
        ----------
        iobj:
            Index of the object
        weight_type: string, optional
            Weight type. can be one of
                'weight': the actual weight map
                'uberseg': uberseg modified weight map
            Default is 'weight'

        returns
        -------
        an ngmix ObsList
        """

        import ngmix
        obslist=ngmix.ObsList()
        for icut in xrange(self._cat['ncutout'][iobj]):
            obs=self.get_obs(iobj, icut, weight_type=weight_type)
            obslist.append(obs)

        obslist.meta['flux'] = obs.meta['flux']
        obslist.meta['T'] = obs.meta['T']
        return obslist

    def get_obs(self, iobj, icutout, weight_type='weight'):
        """
        get an ngmix Observation

        parameters
        ----------
        iobj:
            Index of the object
        icutout:
            Index of the cutout for this object.
        weight_type: string, optional
            Weight type. can be one of
                'weight': the actual weight map
                'uberseg': uberseg modified weight map
            Default is 'weight'

        returns
        -------
        an ngmix Observation
        """

        import ngmix
        im=self.get_cutout(iobj, icutout, type='image')
        bmask=self.get_cutout(iobj, icutout, type='bmask')
        jd=self.get_jacobian(iobj, icutout)


        if weight_type=='uberseg':
            wt=self.get_uberseg(iobj, icutout)
        elif weight_type=='weight':
            wt=self.get_cutout(iobj, icutout, type='weight')
        else:
            raise ValueError("bad weight type '%s'" % weight_type)

        jacobian=ngmix.Jacobian(
            row=jd['row0'],
            col=jd['col0'],
            dudrow=jd['dudrow'],
            dudcol=jd['dudcol'],
            dvdrow=jd['dvdrow'],
            dvdcol=jd['dvdcol'],
        )
        c=self._cat

        scale=jacobian.get_scale()
        x2=c['x2'][iobj]
        y2=c['y2'][iobj]
        T = (x2 + y2)
        flux = c['flux_auto'][iobj]
        meta=dict(
            id=c['id'][iobj],
            T=T,
            flux=flux,
            index=iobj,
            number=c['number'][iobj],
            icut=icutout,
            cutout_index=icutout,
            file_id=c['file_id'][iobj,icutout],
            orig_row=c['orig_row'][iobj, icutout],
            orig_col=c['orig_col'][iobj, icutout],
            orig_start_row=c['orig_start_row'][iobj, icutout],
            orig_start_col=c['orig_start_col'][iobj, icutout],
            orig_end_row=c['orig_end_row'][iobj, icutout],
            orig_end_col=c['orig_end_col'][iobj, icutout],
        )
        return ngmix.Observation(
            im,
            weight=wt,
            bmask=bmask,
            meta=meta,
            jacobian=jacobian,
        )
        
    @property
    def size(self):
        return self._cat.size

    def _check_indices(self, iobj, icutout=None):
        if iobj >= self._cat.size:
            raise ValueError("object index should be within "
                             "[0,%s)" % self._cat.size)

        ncutout=self._cat['ncutout'][iobj]
        if ncutout==0:
            raise ValueError("object %s has no cutouts" % iobj)

        if icutout is not None:
            if icutout >= ncutout:
                raise ValueError("requested cutout index %s for "
                                 "object %s should be in bounds "
                                 "[0,%s)" % (icutout,iobj,ncutout))


class MEDSifier(object):
    def __init__(self, datalist):
        """
        very simple MEDS maker for images. Assumes the images
        line up, have constant noise, are sky subtracted, 
        and have a simple pixel scale

        The images are added together to make a detection image and sep is run

        parameters
        ----------
        datalist: list
            List of dicts with entries
                image
                noise
                scale
        """
        self.datalist=datalist

        self._set_detim()
        self._run_sep()

    def get_meds(self, band):
        """
        get fake MEDS interface to the specified band
        """
        d=self.datalist[band]
        return MEDSInterface(
            d['image'],
            d['weight'],
            self.seg,
            self.bmask,
            self.cat,
        )

    def _get_image_vars(self):
        vars=[]
        for d in self.datalist:
            w=np.where(d['weight'] > 0)
            medw=np.median(d['weight'][w])
            vars.append(1/medw)
        return np.array(vars)

    def _set_detim(self):
        dlist=self.datalist
        nim=len(dlist)

        detim=dlist[0]['image'].copy()
        detim *= 0

        vars = self._get_image_vars()
        weights = 1.0/vars
        wsum = weights.sum()
        detnoise = np.sqrt(1/wsum)

        weights /= wsum

        for i,d in enumerate(dlist):
            detim += d['image']*weights[i]

        self.detim=detim
        self.detnoise=detnoise

    def _run_sep(self):
        import sep
        THRESH=1.2 # in sky sigma
        #THRESH=1.5 # in sky sigma
        objs, seg = sep.extract(
            self.detim,
            THRESH,
            err=self.detnoise,
            deblend_cont=0.0001,
            segmentation_map=True,
        )

        kronrad, krflag = sep.kron_radius(
            self.detim,
            objs['x'],
            objs['y'],
            objs['a'],
            objs['b'],
            objs['theta'],
            6.0,
        )
        flux_auto, fluxerr_auto, flag_auto = \
            sep.sum_ellipse(
                self.detim,
                objs['x'],
                objs['y'],
                objs['a'],
                objs['b'],
                objs['theta'],
                2.5*kronrad,
                subpix=1,
            )
        objs['flag'] |= krflag

        # should bail now if fails I think

        # what we did in DES, but note threshold above
        # is 1 as opposed to wide survey. deep survey
        # was even lower, 0.8?

        # used half light radius
        PHOT_FLUXFRAC = 0.5

        flux_radius, frflag = sep.flux_radius(
            self.detim,
            objs['x'],
            objs['y'],
            6.*objs['a'],
            PHOT_FLUXFRAC,
            normflux=flux_auto,
            subpix=5,
        )
        objs['flag'] |= frflag  # combine flags into 'flag'



        ncut=2 # need this to make sure array
        new_dt=[
            ('id','i8'),
            ('number','i4'),
            ('ncutout','i4'),
            ('flux_auto','f4'),
            ('fluxerr_auto','f4'),
            ('flux_radius','f4'),
            ('isoarea_image','f4'),
            ('iso_radius','f4'),
            ('box_size','i4'),
            ('file_id','i8',ncut),
            ('orig_row','f4',ncut),
            ('orig_col','f4',ncut),
            ('orig_start_row','i8',ncut),
            ('orig_start_col','i8',ncut),
            ('orig_end_row','i8',ncut),
            ('orig_end_col','i8',ncut),
            ('cutout_row','f4',ncut),
            ('cutout_col','f4',ncut),
            ('dudrow','f8',ncut),
            ('dudcol','f8',ncut),
            ('dvdrow','f8',ncut),
            ('dvdcol','f8',ncut),
        ]
        cat=eu.numpy_util.add_fields(objs, new_dt)
        cat['id'] = np.arange(cat.size)
        cat['number'] = np.arange(1,cat.size+1)
        cat['ncutout'] = 1
        cat['flux_auto'] = flux_auto
        cat['fluxerr_auto'] = fluxerr_auto
        cat['flux_radius'] = flux_radius
        wcs=self.datalist[0]['wcs']
        cat['dudrow'][:,0] = wcs.dudy
        cat['dudcol'][:,0] = wcs.dudx
        cat['dvdrow'][:,0] = wcs.dvdy
        cat['dvdcol'][:,0] = wcs.dvdx


        # use the number of pixels in the seg map as the iso area
        for i in xrange(objs.size):
            w=np.where(seg == (i+1))
            print(i,"found",w[0].size)
            cat['isoarea_image'][i] = w[0].size

        cat['iso_radius'] = np.sqrt(cat['isoarea_image'].clip(min=1)/np.pi)

        RAD_MIN=4 # for box size calculations
        BOX_PADDING=2
        MIN_BOX_SIZE=16
        box_size = (2*cat['iso_radius'].clip(min=RAD_MIN) + BOX_PADDING).astype('i4')
        box_size = box_size.clip(min=MIN_BOX_SIZE,out=box_size)
        wb,=np.where( (box_size % 2) != 0 )
        if wb.size > 0:
            box_size[wb] += 1
        half_box_size = box_size//2

        maxrow,maxcol=self.detim.shape
        cat['box_size'] = 2*cat['iso_radius'] + BOX_PADDING

        cat['orig_row'][:,0] = cat['y']
        cat['orig_col'][:,0] = cat['x']

        orow = cat['orig_row'][:,0].astype('i4')
        ocol = cat['orig_col'][:,0].astype('i4')

        ostart_row = orow - half_box_size + 1
        ostart_col = ocol - half_box_size + 1
        oend_row   = orow + half_box_size + 1 # plus one for slices
        oend_col   = ocol + half_box_size + 1

        ostart_row.clip(min=0, out=ostart_row)
        ostart_col.clip(min=0, out=ostart_col)
        oend_row.clip(max=maxrow, out=oend_row)
        oend_col.clip(max=maxcol, out=oend_col)

        # could result in smaller than box_size above
        cat['orig_start_row'][:,0] = ostart_row
        cat['orig_start_col'][:,0] = ostart_col
        cat['orig_end_row'][:,0] = oend_row
        cat['orig_end_col'][:,0] = oend_col
        cat['cutout_row'][:,0] = cat['orig_row'][:,0] - cat['orig_start_row'][:,0]
        cat['cutout_col'][:,0] = cat['orig_col'][:,0] - cat['orig_start_col'][:,0]


        self.seg=seg
        self.bmask=np.zeros(seg.shape, dtype='i4')
        self.cat=cat

def fitpsf(psf_obs):
    am=ngmix.admom.run_admom(psf_obs, 4.0)
    gmix=am.get_gmix()
    return gmix

def get_psf_obs(psfim, jacobian):
    cen=(np.array(psfim.shape)-1.0)/2.0
    j=jacobian.copy()
    j.set_cen(row=cen[0], col=cen[1])

    psf_obs = ngmix.Observation(
        psfim,
        weight=psfim*0+1,
        jacobian=j
    )

    gmix=fitpsf(psf_obs)
    psf_obs.set_gmix(gmix)

    return psf_obs


def test(dim=2000):
    import galsim
    import biggles
    import images

    rng=np.random.RandomState()

    nobj=4
    #nobj=2
    nknots=100
    knot_flux_frac=0.001
    nknots_low, nknots_high=1,100

    nband=3
    noises=[0.0005,0.001,0.0015]
    scale=0.263

    psf=galsim.Gaussian(fwhm=0.9)
    all_band_obj=[]
    dims=64,64
    flux_low, flux_high=0.5,1.5
    r50_low,r50_high=0.1,2.0
    #dims=256,256
    #flux_low, flux_high=50.0,50.0
    #r50_low,r50_high=4.0,4.0

    fracdev_low, fracdev_high=0.001,0.99

    bulge_colors = np.array([0.5, 1.0, 1.5])
    disk_colors = np.array([1.25, 1.0, 0.75])
    knots_colors = np.array([1.5, 1.0, 0.5])

    #bulge_colors /= bulge_colors.sum()
    #disk_colors /= disk_colors.sum()
    #knots_colors /= knots_colors.sum()

    sigma=dims[0]/2.0/4.0*scale
    maxrad=dims[0]/2.0/2.0 * scale
    for i in xrange(nobj):

        nknots=int(rng.uniform(low=nknots_low, high=nknots_high))

        r50=rng.uniform(low=r50_low, high=r50_high)
        flux = rng.uniform(low=flux_low, high=flux_high)

        #dx,dy=rng.uniform(low=-3.0, high=3.0, size=2)
        dx,dy=rng.normal(scale=sigma, size=2).clip(min=-maxrad, max=maxrad)

        g1d,g2d=rng.normal(scale=0.2, size=2).clip(max=0.5)
        g1b=0.5*g1d+rng.normal(scale=0.02)
        g2b=0.5*g2d+rng.normal(scale=0.02)

        fracdev=rng.uniform(low=fracdev_low, high=fracdev_high)

        flux_bulge = fracdev*flux
        flux_disk  = (1-fracdev)*flux
        flux_knots = nknots*knot_flux_frac*flux_disk
        print("fracdev:",fracdev,"nknots:",nknots)

        bulge_obj = galsim.DeVaucouleurs(
            half_light_radius=r50
        ).shear(g1=g1b,g2=g2b)

        disk_obj = galsim.Exponential(
            half_light_radius=r50
        ).shear(g1=g1d,g2=g2d)

        knots_obj = galsim.RandomWalk(
            npoints=nknots,
            profile=disk_obj,
            #half_light_radius=r50
        )#.shear(g1=g1d,g2=g2d)


        band_objs = []
        for band in xrange(nband):
            band_disk=disk_obj.withFlux(flux_disk*disk_colors[band])
            band_bulge=bulge_obj.withFlux(flux_bulge*bulge_colors[band])
            band_knots=knots_obj.withFlux(flux_knots*knots_colors[band])
            #print(band_disk.flux, band_bulge.flux, band_knots.flux)

            obj = galsim.Sum(band_disk, band_bulge, band_knots).shift(dx=dx, dy=dy)
            #obj = galsim.Sum(band_disk, band_bulge).shift(dx=dx, dy=dy)
            obj=galsim.Convolve(obj, psf)
            band_objs.append( obj )


        all_band_obj.append( band_objs )

    jacob=ngmix.DiagonalJacobian(
        row=0,
        col=0,
        scale=scale,
    )
    wcs=jacob.get_galsim_wcs()
    psfim = psf.drawImage(wcs=wcs).array
    psf_obs=get_psf_obs(psfim, jacob)

    dlist=[]
    for band in xrange(nband):
        band_objects = [ o[band] for o in all_band_obj ]
        obj = galsim.Sum(band_objects)

        im = obj.drawImage(nx=dims[1], ny=dims[0], wcs=wcs).array
        #if band==0:
        #    im = obj.drawImage(scale=scale).array
        #    dims=im.shape
        #else:
        #    im = obj.drawImage(nx=dims[1], ny=dims[0], scale=scale).array
        im = obj.drawImage(nx=dims[1], ny=dims[0], scale=scale).array

        im += rng.normal(scale=noises[band], size=im.shape)
        wt = im*0 + 1.0/noises[band]**2

        dlist.append(
            dict(
                image=im,
                weight=wt,
                wcs=wcs,
            )
        )

    tab=biggles.Table(1,2)
    rgb=images.get_color_image(
        #imi*fac,imr*fac,img*fac,
        dlist[2]['image'].transpose(),
        dlist[1]['image'].transpose(),
        dlist[0]['image'].transpose(),
        nonlinear=0.1,
    )
    rgb *= 1.0/rgb.max()
    #images.view(rgb)
 
    mer=MEDSifier(dlist)
    tab[0,0] = images.view_mosaic(
        [rgb,
         mer.seg,
         mer.detim],
        titles=['image','seg','detim'],
        show=False,
        #dims=[dim, dim],
    )

    mg=mer.get_meds(0)
    mr=mer.get_meds(1)
    mi=mer.get_meds(2)
    nobj=mg.size

    imlist=[]
    list_of_obs=[]
    for i in xrange(nobj):

        img=mg.get_cutout(i,0)
        imr=mr.get_cutout(i,0)
        imi=mi.get_cutout(i,0)

        gobslist=mg.get_obslist(i,weight_type='uberseg')
        robslist=mr.get_obslist(i,weight_type='uberseg')
        iobslist=mi.get_obslist(i,weight_type='uberseg')
        mbo=ngmix.MultiBandObsList()
        mbo.append(gobslist)
        mbo.append(robslist)
        mbo.append(iobslist)

        list_of_obs.append(mbo)

        rgb=images.get_color_image(
            #imi*fac,imr*fac,img*fac,
            imi.transpose(),
            imr.transpose(),
            img.transpose(),
            nonlinear=0.1,
        )
        rgb *= 1.0/rgb.max()
        imlist.append(rgb)

    for mbo in list_of_obs:
        for obslist in mbo:
            for obs in obslist:
                obs.set_psf(psf_obs)

    prior=mof.get_mof_prior(list_of_obs, "bdf", rng)
    mof_fitter=mof.MOFStamps(
        list_of_obs,
        "bdf",
        prior=prior,
    )
    band=2
    guess=mof.get_stamp_guesses(list_of_obs, band, "bdf", rng)
    mof_fitter.go(guess)
    return

    #images.view(rgb)
    tab[0,1]=images.view_mosaic(imlist,show=False)

    tab.show(width=dim*2, height=dim)


    imlist=[]
    for i in xrange(nobj):

        im=mg.get_uberseg(i,0)
        imlist.append(im)

    #images.view(rgb)
    images.view_mosaic(imlist)

    

