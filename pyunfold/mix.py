
from __future__ import division, print_function
import numpy as np
from scipy.sparse import coo_matrix, diags
import torch

from .utils import safe_inverse


class Mixer(object):
    """DAgostini Bayesian Mixer Class
    """
    def __init__(self, data=None, data_err=None, efficiencies=None,
                 efficiencies_err=None, response=None, response_err=None,
                 cov_type='multinomial'):
        # Input validation
        if data.shape[0] != response.shape[0]:
            err_msg = ('Inconsistent number of effect bins. Observed data '
                       'has {} effect bins, while response matrix has {} '
                       'effect bins.'.format(data.shape[0], response.shape[0]))
            raise ValueError(err_msg)

        if response.ndim != 2:
            raise ValueError('Response matrix must be 2-dimensional, but got '
                             'a {}-dimensional response matrix '
                             'instead'.format(response.ndim))

        # Normalized P(E|C)
        self.pec = response
        self.cEff = efficiencies
        self.NEobs = data

        # Number of Cause and Effect Bins
        dims = self.pec.shape
        self.cbins = dims[1]
        self.ebins = dims[0]
        self.cEff_inv = safe_inverse(self.cEff)
        # Mixing Matrix
        self.Mij = torch.zeros(dims, dtype=torch.double).to_sparse()

        self.cov = CovarianceMatrix(data=data,
                                    data_err=data_err,
                                    efficiencies=efficiencies,
                                    efficiencies_err=efficiencies_err,
                                    response=response,
                                    response_err=response_err,
                                    cov_type=cov_type)

    def get_cov(self):
        """Covariance Matrix
        """
        cvm = self.cov.get_cov()
        return cvm

    def get_stat_err(self):
        """Statistical Errors
        """
        cvm = self.cov.getVc0()
        err = torch.sqrt(cvm.to_dense().diagonal()).to_sparse() #np.sqrt(cvm.diagonal())
        return err

    def get_MC_err(self):
        """MC (Systematic) Errors
        """
        cvm = self.cov.getVc1()
        err = torch.sqrt(cvm.to_dense().diagonal())
        return err

    def smear(self, n_c):
        """Smear Calculation - the Bayesian Mixer!

        Only needs input prior, n_c, to unfold
        """
        if len(n_c) != self.cbins:
            err_msg = ('Trying to unfold with the wrong number of causes. '
                       'Response matrix has {} cause bins, while prior '
                       'has {} cause bins.'.format(self.cbins, len(n_c)))
            raise ValueError(err_msg)

        # Bayesian Normalization Term (denominator)
        f_norm = self.pec @ n_c
        f_inv = safe_inverse(f_norm)
        n_c_eff = n_c * self.cEff_inv

        # Unfolding (Mij) Matrix at current step
        Mij = self.pec * n_c_eff * f_inv.reshape(-1, 1)

        # Estimate cause distribution via Mij
        n_c_update = self.NEobs @ Mij

        # The status quo
        self.Mij = Mij
        self.cov.set_current_state(self.Mij, f_norm, n_c_update, n_c)

        return n_c_update


class CovarianceMatrix(object):
    """Covariance Base Class
    """
    def __init__(self, data=None, data_err=None, efficiencies=None,
                 efficiencies_err=None, response=None, response_err=None,
                 cov_type='multinomial'):

        # Normalized P(E|C)
        self.pec = response
        self.pec_err = response_err
        # Errors on P(E|C), for Vc1
        if cov_type.lower() not in ['multinomial', 'poisson']:
            raise ValueError('Invalid pec_cov_type entered: {}. Must be '
                             'either "multinomial" or "poisson".'.format(cov_type))
        self.pec_cov_type = cov_type.lower()
        self.cEff = efficiencies
        self.cEff_inv = safe_inverse(self.cEff)
        # Effective number of sim events
        efficiencies_err_inv = safe_inverse(efficiencies_err)
        NCmc = (efficiencies * efficiencies_err_inv)**2
        self.NCmc = NCmc
        self.NEobs = data
        self.NEobs_err = data_err
        # Total number of observed effects
        self.nobs = self.NEobs.sum().item()

        # Number of cause and effect eins
        dims = self.pec.shape
        self.cbins = dims[1]
        self.ebins = dims[0]
        # Adye propagating derivative
        self.dcdn = torch.zeros(dims, dtype=torch.double).to_sparse()
        self.dcdP = torch.zeros((self.cbins, self.cbins * self.ebins), dtype=torch.double).to_sparse()
        # Counter for number of iterations
        self.counter = 0

    def set_current_state(self, Mij, f_norm, n_c, n_c_prev):
        """Set the Current State of dcdn and dcdP
        """
        # D'Agostini Form (and/or First Term of Adye)
        dcdP = self._initialize_dcdP(Mij, f_norm, n_c, n_c_prev)
        dcdn = Mij.clone()
        # Add Adye propagation corrections
        if self.counter > 0:
            dcdn, dcdP = self._adye_propagation_corrections(dcdP, Mij, n_c, n_c_prev)

        # Set current derivative matrices
        self.dcdn = dcdn
        self.dcdP = dcdP
        # On to next iteration
        self.counter += 1

    def _initialize_dcdP(self, Mij, f_norm, n_c, n_c_prev):
        cbins = self.cbins
        ebins = self.ebins

        NE_F_R = self.NEobs * safe_inverse(f_norm)

        dcdP = torch.empty((cbins, cbins * ebins)).double()
        # (ti, ec_j + tk) elements
        tk = np.arange(0, cbins)
        for ej in np.arange(0, ebins):
            A = torch.outer(-NE_F_R[ej] * Mij.to_dense()[ej, :], n_c_prev)
            for ti in np.arange(0, cbins):
                dcdP[ti, ej * cbins + tk] = A[ti, tk]

        # (ti, ec_j + ti) elements
        ti = np.arange(0, cbins)
        A = (torch.outer(n_c_prev, NE_F_R) - n_c[:, None]) * self.cEff_inv[:, None]
        for ej in np.arange(0, ebins):
            dcdP[ti, ej * cbins + ti] += A[ti, ej]

        return dcdP.to_sparse()

    def _adye_propagation_corrections(self, dcdP, Mij, n_c, n_c_prev):
        dcdn = Mij.clone()

        # Get previous derivatives
        dcdn_prev = self.dcdn
        dcdP_prev = self.dcdP

        n_c_prev_inv = safe_inverse(n_c_prev)
        # Ratio of updated n_c to n_c_prev
        nc_r = n_c * n_c_prev_inv
        # Efficiency ratio of n_c_prev
        e_r = self.cEff * n_c_prev_inv

        # Calculate extra dcdn terms
        M1 = dcdn_prev * nc_r
        M2 = -Mij * e_r
        M2 = M2.T * self.NEobs
        M3 = M2 @ dcdn_prev
        dcdn += Mij @ M3
        dcdn += M1

        # Calculate extra dcdP terms (from unfolding doc)
        At = Mij.T * self.NEobs
        B = Mij * e_r
        C = At @ B
        dcdP_Upd = C @ dcdP_prev
        dcdP += (dcdP_prev.T * nc_r).T - dcdP_Upd

        return dcdn, dcdP

    def getVcd(self):
        """Get Covariance Matrix of N(E), ie from Observed Effects
        """
        # Vcd = torch.diag(self.NEobs_err * self.NEobs_err).to_sparse()
        diagonal = self.NEobs_err * self.NEobs_err
        Vcd = torch.sparse.spdiags(diagonal[:, None].T, torch.zeros(1).long(), (diagonal.shape[0], diagonal.shape[0]))
        return Vcd

    def getVc0(self):
        """Get full Vc0 (data) contribution to cov matrix
        """
        # Get derivative
        dcdn = self.dcdn
        # Get NObs covariance
        Vcd = self.getVcd()
        # Set data covariance
        Vc0 = dcdn.T @ Vcd @ dcdn #dcdn.T.dot(Vcd).dot(dcdn)

        return Vc0

    def getVcPP(self):
        """Get Covariance Matrix of P(E|C), ie from MC
        """
        cbins = self.cbins
        ebins = self.ebins

        # Poisson covariance matrix
        if self.pec_cov_type == 'poisson':
            CovPP = poisson_covariance(ebins, cbins, self.pec_err)
        # Multinomial covariance matrix
        elif self.pec_cov_type == 'multinomial':
            raise NotImplementedError
            # nc_inv = safe_inverse(self.NCmc)
            # CovPP = multinomial_covariance(ebins, cbins, nc_inv, self.pec)

        return CovPP

    def getVc1(self):
        """Get full Vc1 (MC) contribution to cov matrix
        """
        # Get NObs covariance
        CovPP = self.getVcPP()
        # Get derivative
        dcdP = self.dcdP
        # Set MC covariance
        Vc1 = dcdP @ CovPP @ dcdP.T #dcdP.dot(CovPP).dot(dcdP.T)
        return Vc1

    def get_cov(self):
        """Get full covariance matrix
        """
        # Get Contributions from Vc0 and Vc1
        Vc0 = self.getVc0()
        Vc1 = self.getVc1()
        # Full Covariance Matrix
        Vc = Vc0 + Vc1
        return Vc


def poisson_covariance(ebins, cbins, pec_err):
    # Poisson covariance matrix
    diagonal = pec_err.to_dense().ravel()
    diagonal *= diagonal
    #return torch.diag(pec_err.to_dense().ravel() * pec_err.to_dense().ravel()).to_sparse()
    return torch.sparse.spdiags(diagonal[:, None].T, torch.zeros(1).long(), (diagonal.shape[0], diagonal.shape[0]))


def multinomial_covariance(ebins, cbins, nc_inv, pec):
    CovPP = np.zeros((cbins * ebins, cbins * ebins))
    ti = np.arange(0, cbins)
    A = nc_inv * pec * (1 - pec)
    for ej in np.arange(0, ebins):
        ejc = ej * cbins
        CovPP[ejc+ti, ejc+ti] = A[ej]
        cov = -nc_inv * pec[ej, :] * pec
        for ek in np.arange(ej + 1, ebins):
            CovPP[ejc + ti, ek * cbins + ti] = cov[ek, :]
            CovPP[ek * cbins + ti, ejc + ti] = cov[ek, :]

    return CovPP
