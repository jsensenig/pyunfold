"""
   Utility functions provided for
   config file parsing, test statistic methods,
   and regularization.
"""

from __future__ import division, print_function
import sys
import ConfigParser
import numpy as np
from scipy.special import gammainc as gammaq
from scipy.special import gammaln as lgamma
from scipy.stats import kstwobign
import matplotlib as mpl
import matplotlib.pyplot as plt

import ROOT
from ROOT import TF1, TH1F
from ROOT import gROOT, gSystem

gROOT.Reset()
# Turn Off TCanvas Warning at DATA.Fit
ROOT.gErrorIgnoreLevel = ROOT.kWarning


def none_to_empty_list(*args):
    """Replaces None inputs with an empty list

    Examples
    --------
    Single input case

    >>> none_to_empty_list(None)
    []

    Multiple input case

    >>> a, b, c = None, 'woo', 34
    >>> none_to_empty_list(a, b, c)
    [[], 'woo', 34]
    """
    outputs = []
    for arg in args:
        outputs.append(arg if arg is not None else [])
    if len(outputs) == 1:
        return outputs[0]
    else:
        return outputs


# Inverse of Numpy Array; Set Elements to 0 if 0
def safe_inverse(x):
    """Safely inverts the elements in x

    Parameters
    ----------
    x : array_like
        Input array to take the inverse of (i.e. 1 / x).

    Returns
    -------
    inv : numpy.ndarray
        Inverse of input array with inf set to zero.

    Examples
    --------
    >>> a = [1, 2, 3, 0, 4]
    >>> safe_inverse(a)
    array([1.        , 0.5       , 0.33333333, 0.        , 0.25      ])
    """
    x = np.asarray(x)
    is_zero = x == 0
    with np.errstate(divide='ignore'):
        inv = 1 / x
    inv[is_zero] = 0

    return inv


# Class for Managing Configuration Files
class ConfigFM:
    """Class for Managing Configuration Files
    """
    def __init__(self, file):
        self.parser = ConfigParser.ConfigParser()
        self.parser.readfp(open(file))
        self.parser.read(file)

    def get(self, sec, opt, is_bool=False, is_quiet=True, default=None,
            cast=None):
        response = default
        if sec in self.parser.sections():
            if opt.lower() in self.parser.options(sec):
                try:
                    response = self.parser.getboolean(sec, opt) if is_bool else self.parser.get(sec, opt)
                except Exception as e:
                    print('Exception: {}'.format(e))
            elif(not is_quiet and default is None):
                raise ValueError("Requested option {} not present in section {}".format(opt, sec))
        elif(not is_quiet):
            raise ValueError("Requested section {} not found".format(sec))
        if(response is not None and cast is not None):
            try:
                newresp = cast(response)
                return newresp
            except Exception as e:
                return response

        return response

    def get_boolean(self, sec, opt, is_quiet=True, default=None):
        return self.get(sec, opt, True, is_quiet, default)


def JeffreysPrior(norm, xarray):
    """Jeffreys Prior
    """
    # All cause bins are given equal probability.
    # Best prior for x-ranges spanning decades.
    ilen = len(xarray)
    ln_factor = np.log(xarray[ilen - 1] / xarray[0])
    jprior = norm / (ln_factor * xarray)

    return jprior


def UserPrior(FuncList, xarray, norm):
    """User provided prior function
    """
    nAnalysisBins = len(FuncList)
    prior = []
    for ibin in range(nAnalysisBins):
        FuncString = FuncList[ibin]
        ixarray = xarray[ibin].copy()
        if (FuncString.lower() == "jeffreys"):
            iprior = JeffreysPrior(norm, ixarray)
        else:
            exec("def func(x): return {}".format(FuncString))
            iprior = func(ixarray)
        if prior == []:
            prior = iprior
        else:
            prior = np.append(prior, iprior)

    return prior


class DataDist:
    """Class to define generic distribution w/assoc labels & axes
    """
    def __init__(self, name="", data=None, error=None, axis=None, edges=None,
                 xlabel="", ylabel="", units="", **kwargs):
        data, error, axis, edges = none_to_empty_list(data, error, axis, edges)
        self.name = name
        self.data = data
        self.nbins = len(data)
        self.error = error
        self.axis = axis
        self.edges = edges
        self.width = np.diff(edges)
        self.xlab = xlabel
        self.ylab = ylabel
        self.units = units
        # Systematic and Statistical Error
        self.sterr = np.zeros(self.nbins)
        self.syerr = np.zeros(self.nbins)
        self.CheckArrays()

    # Ensure that arrays are of proper lenghts
    def CheckArrays(self):
        if self.nbins != len(self.error):
            raise ValueError("{} data and error arrays unequal length!".format(self.name))
        if self.nbins != len(self.axis):
            raise ValueError("{} data and axis arrays unequal length!".format(self.name))
        if self.nbins != len(self.edges) - 1:
            raise ValueError("{} data and edges arrays improper length!".format(self.name))

    # Getter functions for data and error arrays
    def getData(self):
        return self.data

    def getError(self):
        return self.error

    # Setter functions systematic and statistical error arrays
    def setStatErr(self, staterr):
        self.sterr = staterr.copy()

    def setSysErr(self, syserr):
        self.syerr = syserr.copy()


class PowerLaw(object):
    """Class to define power law and further functionality
    """
    def __init__(self, name="ExamplePowerLaw", Nevents=1e5, Index=2.7,
                 Xlim=(1e12, 1e15)):
        self.name = name
        self.Nevents = Nevents
        self.idx = Index
        self.xlo = Xlim[0]
        self.xhi = Xlim[1]

    # Get the number of events in a x-range
    def getN(self, xlo, xhi):
        if (self.idx == 1):
            numer = np.log(xhi) - np.log(xlo)
            denom = np.log(self.xhi) - np.log(self.xlo)
        else:
            g = 1 - self.idx
            numer = xhi**g - xlo**g
            denom = self.xhi**g - self.xlo**g
        return numer / denom

    # Fill a frequency spectrum
    # Can either draw from distribution randomly or
    # keep analytical form of dist scaled by Nevents
    def Fill(self, X=None, method="rand"):
        N = np.zeros(len(X) - 1)
        if method == "rand":
            rand_E = self.Random()
            N, bin_edges = np.histogram(rand_E, X)
        elif method == "analytic":
            for i in xrange(0, len(N)):
                N[i] = self.Nevents*self.getN(X[i], X[i + 1])
                N[i] = np.int(N[i])
        return N

    # Draw a random number or array of random
    # numbers distributed via the parent pl dist
    def Random(self, N=None):
        if N is None:
            N = self.Nevents
        g = 1 - self.idx
        y = np.random.rand(N)
        pl_rand = (self.xhi**g - self.xlo**g) * y + self.xlo**g
        pl_rand = pl_rand**(1 / g)
        return pl_rand


class TestStat(object):
    """Common base class for test statistic methods
    """
    def __init__(self, name="TestStat", tol=None, Xaxis=None, TestRange=None,
                 verbose=False, **kwargs):
        Xaxis, TestRange = none_to_empty_list(Xaxis, TestRange)
        # TS Name
        self.name = name
        # User Defined TS Tolerance
        self.tol = tol
        # User Provided Xaxis and Test Range
        self.Xaxis = Xaxis.copy()
        self.TSRange = TestRange
        self.IsRangeTS = False
        self.TSbins = self.SetTestRangeBins()
        # Verbose Functionality
        self.verbose = verbose
        self.printProbMessage = True
        self.printStatsHeader = True
        if verbose:
            self.PrintName()
        # Initialize Unnatural TS data
        self.stat = -1
        self.delstat = 0
        self.prob = -1
        self.dof = -1
        self.dofSet = False

    def SetTestRangeBins(self):
        bins = [0,-1]
        if (not self.TSRange == []):
            lTSR = len(self.TSRange)
            err_mess = "***\n Test stat range can only have two elements. This has %i. Exiting...***\n"%(lTSR)
            assert (lTSR==2), err_mess
            xlo = self.TSRange[0]
            xhi = self.TSRange[1]
            err_mess = "***\n Test stat limits reversed. xlo must be < xhi. Exiting...***\n"
            assert (xlo<xhi), err_mess

            # Find the bins corresponding to the test range requested
            lobin = np.searchsorted(self.Xaxis,xlo)
            hibin = np.searchsorted(self.Xaxis,xhi)
            bins = [lobin, hibin]
            self.IsRangeTS = True
        return bins

    def GetArrayRange(self,N1,N2):
        if (self.IsRangeTS):
            NR1 = N1[self.TSbins[0]:self.TSbins[1]]
            NR2 = N2[self.TSbins[0]:self.TSbins[1]]
            return NR1, NR2
        else:
            return N1.copy(), N2.copy()

    # Function Testing Whether TS < tol
    def PassTol(self):
        return (self.stat < self.tol)

    # Set Degrees of Freedom
    def SetDOF(self,dof):
        if (self.dof == -1):
            self.dof = dof

    # Set TS and delTS
    def SetStat(self,stat):
        self.delstat = stat - self.stat
        self.stat = stat

    # Test for Equal Length Distributions
    def TestLengths(self, N1, N2):
        ln1 = len(N1)
        ln2 = len(N2)
        err_mess = "Test Statistic arrays are not equal length. %i != %i. Exiting...\n"%(ln1,ln2)
        assert (ln1 == ln2), err_mess
        if (not self.dofSet):
            self.SetDOF(ln1)
            self.dofSet = True

    # Calculate the TS
    def TSCalc(self, N1, N2):
        """Undefined Test Statistics Calculator
        """
        raise NotImplementedError()

    # Calculate the TS Probability Function
    def Prob(self):
        if (self.verbose and self.printProbMessage):
            print("***No probability function defined for this method, %s. It's ok...***"%self.name)
            self.printProbMessage = False

    def PrintName(self):
        print("\nTest Statistic Method: ", self.__doc__)
        print("Test statistic only valid in range: %e %e\n"%(self.TSRange[0],self.TSRange[1]))

    def PrintStats(self):
        if (self.printStatsHeader):
            print("\t\tStat\tdelStat\tProb")
            print("\t\t%.04f\t%.04f\t%.03f\n"\
            %(self.stat,self.delstat,self.prob))
            self.printStatsHeader = False
        else:
            print("\t\t%.04f\t%.04f\t%.03f"\
            %(self.stat,self.delstat,self.prob))

    # Calculate and return TS Data
    def GetStats(self, N1, N2):
        # Calculate the Test Statistic
        self.TSCalc(N1,N2)
        # Calculate the Probability of TS
        self.Prob()
        # Print if default
        if (self.verbose):
            self.PrintStats()
        # Return TS Data
        return self.stat, self.delstat, self.prob


class Chi2(TestStat):
    """Reduced Chi2 Test Statistic
    """
    def TSCalc(self, N1, N2):
        N1, N2 = self.GetArrayRange(N1,N2)
        self.TestLengths(N1,N2)
        n1 = np.sum(N1)
        n2 = np.sum(N2)

        h_sum = N1+N2
        # Don't divide by 0...
        h_sum[(h_sum<1)] = 1.
        h_dif = n2*N1-n1*N2
        h_quot = h_dif*h_dif/h_sum

        stat = np.sum(h_quot)/(n1*n2)/self.dof

        self.SetStat(stat)

    def Prob(self):
        # Chi2 Probability Function
        self.prob = gammaq(0.5*self.dof,0.5*self.stat)

# Bayes Factor Test - comparing two binned distributions
# Method B - Pfendner et al.
# Recall that lgamma(x) = log ( gamma(x) )
class PF(TestStat):
    'Bayes Factor Test Statistic'
    def TSCalc(self, N1, N2):
        N1, N2 = self.GetArrayRange(N1,N2)
        self.TestLengths(N1,N2)
        lnB = 0
        n1 = np.sum(N1)
        n2 = np.sum(N2)
        nFactor = lgamma(n1+n2+2) - lgamma(n1+1) - lgamma(n2+1)

        lnB += nFactor
        for i in xrange(0,len(N1)):
            lnB += lgamma(N1[i]+1) + lgamma(N2[i]+1) - lgamma(N1[i]+N2[i]+2)

        self.SetStat(lnB)


# Relative Difference Test - comparing two binned distributions
# Taking the maximal relative difference as TS
class RMD(TestStat):
    'Max Relative Difference Test Statistic'
    def TSCalc(self, N1, N2):
        N1, N2 = self.GetArrayRange(N1,N2)
        self.TestLengths(N1,N2)

        h_sum = N1+N2
        h_sum[(h_sum<1)] = 1.
        h_dif = np.abs(N1-N2)
        h_quot = h_dif/h_sum
        stat = np.max(h_quot)

        self.SetStat(stat)


# Kuiper's Statistic - comparing two binned distributions
# Taken from https://github.com/scipy/scipy/blob/v0.14.0/scipy/stats/stats.py#L3809
# and
# http://docs.scipy.org/doc/scipy-0.13.0/reference/generated/scipy.stats.kstwobign.html
class KS(TestStat):
    """KS Test Statistic
    """
    def TSCalc(self, N1, N2):
        N1, N2 = self.GetArrayRange(N1, N2)
        self.TestLengths(N1, N2)

        n1 = np.sum(N1)
        n2 = np.sum(N2)
        cs1 = np.cumsum(N1)/n1
        cs2 = np.cumsum(N2)/n2

        NE = n1*n2/(n1+n2)

        len1 = len(N1)
        en = np.sqrt(len1/2)
        d = np.max(np.abs(cs1-cs2))
        self.SetStat(d)

    def Prob(self):
        try:
            prob = kstwobign.sf((en+.12+.11/en)*d)
        except:
            prob = 1.0

        # KS Probability Function
        self.prob = prob


def get_ts(name='ks'):
    """Convenience function for retrieving TestStat object

    Parameters
    ----------
    name : {'ks', 'chi2', 'pf', 'rmd'}
        Name of test statistic.

    Returns
    -------
    ts : TestStat
        Test statistics object
    """
    name_to_ts = {"chi2": Chi2,
                  "pf": PF,
                  "rmd": RMD,
                  "ks": KS,
                  }
    if name in name_to_ts:
        ts = name_to_ts[name]
        return ts
    else:
        raise ValueError('Invalid test statisitc, {}, entered. Must be '
                         'in {}'.format(name, name_to_ts.keys()))


class Regularizer:
    'Regularizer Class'
    def __init__(self, name, FitFunc, Range, InitialParams, ParamLo, ParamHi, ParamNames, xarray, xedges, verbose=False, plot=False, **kwargs):

        # Prepare string fnc definition for ROOT TF1 declaration
        funcstring = str(FitFunc)
        funcstring = funcstring.strip('[]')
        funcstring = funcstring.strip("''")

        '''From Initialization Inputs'''
        self.name = name
        self.verbose = verbose
        self.plot = plot
        # Number of data bins and xarray and edges of bins
        self.nbins = len(xarray)
        self.xarray = xarray.copy()
        self.xedges = xedges.copy()
        self.Range = self.SetFitRange(Range)
        # Number of fit params, names, init vals, & limits
        self.nParams = len(InitialParams)
        self.ParamNames = ParamNames
        self.Params = np.asarray(InitialParams,dtype=float)
        self.ParamLimitsLo = np.asarray(ParamLo,dtype=float)
        self.ParamLimitsHi = np.asarray(ParamHi,dtype=float)
        self.ParLimProvided = False
        # Chi2 of fit and degrees of freedom
        self.chi2 = -1
        self.dof = 0
        # Fit function string
        self.FitFunc = funcstring
        self.FIT = TF1("FIT", self.FitFunc, self.Range[0], self.Range[1])

        # Ensure that Fit Function is Initialized Properly
        self.TestLengths(ParamNames,InitialParams,"Parameter Names & Initial Value")
        self.TestFuncString(funcstring)
        # Set the Parameter limits if provided
        self.SetParLimits()

        # self.PrintInitMessage()

    # Initialization Message Print Out
    def PrintInitMessage(self):
        stringFunc = self.FitFunc
        for i in xrange(0,self.nParams):
            stringFunc = stringFunc.replace("%i"%i,self.ParamNames[i])
        stringFunc = stringFunc.replace('[',"")
        stringFunc = stringFunc.replace(']',"")
        print("\nRegularizing %i-parameter function initialized to form: %s"%(self.nParams,stringFunc))
        print("Can only support fit functions with up to 10 parameters.\n")

    def SetFitRange(self,Range):

        if len(Range) == 0:
            Range = np.zeros(2)
            Range[0] = self.xarray[0]
            Range[1] = self.xarray[-1]
        else:
            lR = len(Range)
            err_mess = "\n*** Fit range can only have two elements. This has %i. Exiting...***\n"%(lR)
            assert (lR==2), err_mess
            xlo = Range[0]
            xhi = Range[1]
            err_mess = "\n*** Fit range limits reversed. xlo must be < xhi. Exiting...***\n"
            assert (xlo<xhi), err_mess

        return Range

    def SetParLimits(self):
        # Set Par Limits if provided
        if not len(self.ParamLimitsLo) == 0 and not len(self.ParamLimitsHi) == 0:
            PLo = self.ParamLimitsLo.copy()
            PHi = self.ParamLimitsHi.copy()
            self.TestLengths(PLo,PHi,"Parameter Limit")
            self.TestLengths(PLo,self.Params,"Parameter Limit & Initial Value")
            for i in xrange(0,self.nParams):
                err_mess = "\n*** Regularizer param %i limits reversed. xlo must be < xhi. Exiting...***\n" %(i)
                assert (PLo[i]<PHi[i]), err_mess
                self.ParLimProvided = True

    # Test ROOT String Func Number of Params
    def TestFuncString(self,string):
        num_nums = sum(c.isdigit() for c in string)

        err_mess = "\nRegularizer func has %i parameters != %i requested in InitialParams keyword. Exiting...\n" %(num_nums,self.nParams)
        assert (num_nums == self.nParams), err_mess

        func_pars = np.asarray([float(c) for c in string if c.isdigit()])

        err_mess = "\nRegularizer func definition is funky. Perhaps you counted twice or skipped an integer: %s. Exiting...\n"%(string)
        assert (np.array_equal(func_pars,np.linspace(0,num_nums-1,num_nums))), err_mess

    # Test for Equal Length Arrays
    def TestLengths(self, N1, N2, message):
        ln1 = len(N1)
        ln2 = len(N2)
        err_mess = "\nRegularizer %s arrays are not equal length. %i != %i. Exiting...\n"%(message,ln1,ln2)
        assert (ln1 == ln2), err_mess

    # Get Reduced Chi2 of Fit
    def GetRedChi2(self):
        err_mess = "\nFit not yet performed, dof = %f"%(self.dof)
        assert (not self.dof==0), err_mess

        return self.chi2/self.dof

    # Evaulate fit at points given by xarray
    def FitEval(self, xarray):
        f_eval = np.zeros(len(xarray))
        for j in xrange(0,len(xarray)):
            f_eval[j] = self.FIT.Eval(xarray[j])
        return f_eval

    # Regularization procedure
    def Regularize(self, ydata, yerr=None):

        if yerr is None:
            yerr = []

        # Local variable for cleanliness
        nPar = self.nParams

        self.TestLengths(self.xarray,ydata,"Cause X-axis & Unfolded Data")
        # ROOT object to fit
        DATA = TH1F("data","data",self.nbins,self.xedges)
        for i in xrange(0,self.nbins):
            DATA.SetBinContent(i+1,ydata[i])
            if ( len(yerr)>0 ):
                DATA.SetBinError(i+1,yerr[i])
            else:
                DATA.SetBinError(i+1,np.sqrt(ydata[i]))

        # Prepare fit parameters based on user InitialParams
        # or previous fit if used iteratively
        Pars = np.array(self.Params,dtype=float)
        self.FIT.SetParameters(Pars)

        # Set Par Limits if provided
        if (self.ParLimProvided):
            PLo = self.ParamLimitsLo.copy()
            PHi = self.ParamLimitsHi.copy()
            for i in xrange(0,self.nParams):
                self.FIT.SetParLimits(i,PLo[i],PHi[i])

        Fopts = "R"
        if (not self.verbose):
            Fopts += "Q" # Quiet mode
        DATA.Fit(self.FIT,Fopts)

        # Eval fit at xarray
        f_eval = self.FitEval(self.xarray)

        # Store fit parameters
        for i in xrange(0,nPar):
            self.Params[i] = self.FIT.GetParameter(i)

        # Store chi2 and dof of fit
        self.chi2 = self.FIT.GetChisquare()
        self.dof = self.FIT.GetNDF()

        # Print Results Nicely :)
        if (self.verbose):
            print("=====================")
            print("Fit Parameter Results")
            for i in xrange(0,nPar):
                print(self.ParamNames[i], " = ", self.Params[i])
            print("Red X2: = ", self.GetRedChi2())
            print("=====================")

        # Plot Comparison Nicely :)
        if (self.plot):
            self.Plot(ydata,f_eval)

        # Return fit eval array and fit parameters
        return f_eval, self.Params

    # Plot data and fit
    def Plot(self,data,fitdata):
            fig = plt.figure(figsize=(8,7))
            ax = fig.add_subplot(111)
            mpl.rc("font", family="serif", size=14)
            ax.plot(self.xarray, data, label="data", color='k',ls='--')
            ax.plot(self.xarray, fitdata, label="fit", color='r',ls=':')
            ax.set_xscale("log")
            ax.set_xlabel("X")
            ax.set_xlim([self.xarray[0], self.xarray[-1]])
            ax.set_yscale("log")
            ax.set_ylim([np.min(fitdata), np.max(fitdata)])
            ax.set_ylabel("Y")
            ax.set_title("Compare Data to Fit")
            handles, labels = ax.get_legend_handles_labels()
            leg = ax.legend(handles, labels, fontsize=10, loc="best")
            leg.get_frame().set_linewidth(0)
            plt.show()
