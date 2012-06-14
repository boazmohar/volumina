#!/usr/bin/env python
# -*- coding: utf-8 -*-

#    Copyright 2010, 2011 C Sommer, C Straehle, U Koethe, FA Hamprecht. All rights reserved.
#    
#    Redistribution and use in source and binary forms, with or without modification, are
#    permitted provided that the following conditions are met:
#    
#       1. Redistributions of source code must retain the above copyright notice, this list of
#          conditions and the following disclaimer.
#    
#       2. Redistributions in binary form must reproduce the above copyright notice, this list
#          of conditions and the following disclaimer in the documentation and/or other materials
#          provided with the distribution.
#    
#    THIS SOFTWARE IS PROVIDED BY THE ABOVE COPYRIGHT HOLDERS ``AS IS'' AND ANY EXPRESS OR IMPLIED
#    WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
#    FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE ABOVE COPYRIGHT HOLDERS OR
#    CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
#    CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#    SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
#    ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
#    NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
#    ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#    
#    The views and conclusions contained in the software and documentation are those of the
#    authors and should not be interpreted as representing official policies, either expressed
#    or implied, of their employers.

import numpy

import volumina
from volumina.colorama import Fore, Back, Style

from functools import partial
from PyQt4.QtCore import QRect, QRectF, QMutex, QPointF, Qt, QSizeF
from PyQt4.QtGui import QGraphicsScene, QImage, QTransform, QPen, QColor, QBrush, \
                        QFont, QPainter, QGraphicsItem

from imageSceneRendering import ImageSceneRenderThread

from volumina.tiling import *
import math

import threading

#*******************************************************************************
# I m a g e S c e n e 2 D                                                      *
#*******************************************************************************
class DirtyIndicator(QGraphicsItem):
    """
    Indicates the computation progress of each tile. Each tile can be composed
    of multiple layers and is dirty as long as any of these layer tiles are
    not yet computed/up to date. The number of layer tiles still missing is
    indicated by a 'pie' chart.
    """
    def __init__(self, tiling):
        QGraphicsItem.__init__(self, parent=None)
        self._tiling = tiling
        self._indicate = numpy.zeros(len(tiling))

    def boundingRect(self):
        return self._tiling.boundingRectF()
    
    def paint(self, painter, option, widget):
        dirtyColor = QColor(255,0,0)
        doneColor  = QColor(0,255 ,0)
        painter.setOpacity(0.5)
        painter.save()
        painter.setBrush(QBrush(dirtyColor, Qt.SolidPattern))
        painter.setPen(dirtyColor)

        for i,p in enumerate(self._tiling.rectF):
            if self._indicate[i] == 1.0:
                continue
            w,h = p.width(), p.height()
            r = min(w,h)
            rectangle = QRectF(p.center()-QPointF(r/4,r/4), QSizeF(r/2, r/2));
            startAngle = 0 * 16
            spanAngle  = min(360*16, int((1.0-self._indicate[i])*360.0) * 16)
            painter.drawPie(rectangle, startAngle, spanAngle)

        painter.restore()

    def setTileProgress(self, tileId, progress):
        self._indicate[tileId] = progress
        self.update()

#*******************************************************************************
# I m a g e S c e n e 2 D                                                      *
#*******************************************************************************

class ImageScene2D(QGraphicsScene):
    """
    The 2D scene description of a tiled image generated by evaluating
    an overlay stack, together with a 2D cursor.
    """
    
    @property
    def stackedImageSources(self):
        return self._stackedImageSources
    
    @stackedImageSources.setter
    def stackedImageSources(self, s):
        self._stackedImageSources = s
        s.layerDirty.connect(self._onLayerDirty)
        s.stackChanged.connect(self._onStackChanged)
        s.aboutToResize.connect(self._onAboutToResize)
        s.resizeFinished.connect(self._onResizeFinished)
        self._numLayers = len(s)

    def _onResizeFinished(self, newSize):
          if self._renderThread:
              self._renderThread.start(self._tiling)

    def _onAboutToResize(self, newSize):
        if self._renderThread:
          self._numLayers = newSize
          self.reshapeRequests()
          assert not self._renderThread.isRunning()

        #render thread will be re-started when the stack has actually changed
        #to the new size, which will be when stackChanged is emitted

    @property
    def showDebugPatches(self):
        return self._showDebugPatches
    @showDebugPatches.setter
    def showDebugPatches(self, show):
        self._showDebugPatches = show
        self._invalidateRect()

    @property
    def sceneShape(self):
        """
        The shape of the scene in QGraphicsView's coordinate system.
        """
        return (self.sceneRect().width(), self.sceneRect().height())
    @sceneShape.setter
    def sceneShape(self, sceneShape):
        """
        Set the size of the scene in QGraphicsView's coordinate system.
        sceneShape -- (widthX, widthY),
        where the origin of the coordinate system is in the upper left corner
        of the screen and 'x' points right and 'y' points down
        """   
        self._cancelAll()
            
        assert len(sceneShape) == 2
        self.setSceneRect(0,0, *sceneShape)
        
        #The scene shape is in Qt's QGraphicsScene coordinate system,
        #that is the origin is in the top left of the screen, and the
        #'x' axis points to the right and the 'y' axis down.
        
        #The coordinate system of the data handles things differently.
        #The x axis points down and the y axis points to the right.

        r = self.scene2data.mapRect(QRect(0,0,sceneShape[0], sceneShape[1]))
        sliceShape = (r.width(), r.height())
        
        if self._dirtyIndicator:
            self.removeItem(self._dirtyIndicator)
        del self._dirtyIndicator
        self._dirtyIndicator = None

        if self._renderThread is None:
            self._renderThread = ImageSceneRenderThread(self.stackedImageSources, parent=self)
        else:
            self._renderThread.stop()

        self._tiling = Tiling(sliceShape, self.data2scene)

        self._dirtyIndicator = DirtyIndicator(self._tiling)
        self.addItem(self._dirtyIndicator)
            
        self._renderThread.patchAvailable.connect(self.scheduleRedraw)

        self._onStackChanged()
        
    def scheduleRedraw(self, rect):
        self.invalidate(QRectF(rect), QGraphicsScene.BackgroundLayer)

    def __init__( self, parent=None ):
        QGraphicsScene.__init__( self, parent=parent )
        self._updatableTiles = []

        # tiled rendering of patches
        self._tiling         = None
        self._brushingLayer  = None
        # indicates the dirtyness of each tile
        self._dirtyIndicator = None

        self._renderThread = None
        self._stackedImageSources = None
        self._numLayers = 0 #current number of 'layers'
        self._showDebugPatches = False
        self._requestsNew = None
        self._requestsOld = None
    
        self.data2scene = QTransform(0,1,1,0,0,0) 
        self.scene2data = self.data2scene.transposed()
        
        self._slicingPositionSettled = True
    
        self.destroyed.connect(self.__cleanup)
    
    def __cleanup( self ):
        if self._renderThread:
            self._renderThread.stop()
        

    def _initializePatches(self):
        if not self._renderThread:
          return

        self.reshapeRequests()
        self._brushingLayer  = TiledImageLayer(self._tiling)
        self._renderThread.start(self._tiling)

    def reshapeRequests(self):
        self._renderThread.stop()
        shape = (self._numLayers, len(self._tiling))
        self._requestsOld       = numpy.ndarray(shape, dtype = object)
        self._requestsNew       = numpy.ndarray(shape, dtype = object)
  
    def _numRequestsUnfinished(self, patchNr):
        return len(numpy.where(self._requestsOld[:,patchNr])[0])/float(self._numLayers)
    
    def drawLine(self, fromPoint, toPoint, pen):
        tileId = self._tiling.containsF(toPoint)
        if tileId is None:
            return
       
        p = self._brushingLayer[tileId] 
        p.lock()
        painter = QPainter(p.image)
        painter.setPen(pen)
        
        tL = self._tiling._imageRectF[tileId].topLeft()
        painter.drawLine(fromPoint-tL, toPoint-tL)
        painter.end()
        p.dataVer += 1
        p.unlock()
        self.scheduleRedraw(self._tiling._imageRectF[tileId])

    def _onPatchFinished(self, image, request, patchNr, layerNr, tiling, numLayers):
        reqs_old = self._requestsOld
        reqs_new = self._requestsNew
        if layerNr >= reqs_old.shape[0] or tiling != self._tiling:
            return       
        r = reqs_old[layerNr, patchNr]
        if r == request:
            reqs_old[layerNr, patchNr] = reqs_new[layerNr, patchNr]
            reqs_new[layerNr, patchNr] = None
        elif request == reqs_new[layerNr, patchNr]:
            reqs_old[layerNr, patchNr] = reqs_new[layerNr, patchNr] = None
        else:
            return

        self._renderThread._queue.appendleft((layerNr, patchNr, image, tiling, numLayers))
        self._renderThread._dataPending.set()

    def _requestPatch(self, layerNr, patchNr, tiling):
        if not self._renderThread.isRunning():
            return
        
        lastVisibleLayer = self._stackedImageSources._lastVisibleLayer
        
        numLayers = len(self._stackedImageSources)
        
        if layerNr <= lastVisibleLayer and self._stackedImageSources._layerStackModel[layerNr].visible:
          request = self._stackedImageSources.getImageSource(layerNr).request(tiling._imageRect[patchNr])
          r = self._requestsNew[layerNr, patchNr]

          if r is not None:
              r.cancel()
              self._requestsNew[layerNr, patchNr] = request
          else:
              self._requestsOld[layerNr, patchNr] = request

          request.notify(self._onPatchFinished, request=request, patchNr=patchNr, layerNr=layerNr, tiling=tiling, numLayers=numLayers)

    def _onLayerDirty(self, layerNr, rect):
        if self.views():
            viewportRect = self.views()[0].mapToScene(self.views()[0].rect()).boundingRect()
            viewportRect = QRect(math.floor(viewportRect.x()), math.floor(viewportRect.y()), math.ceil(viewportRect.width()), math.ceil(viewportRect.height()))
            if not rect.isValid():
                rect = viewportRect
                self._updatableTiles = []

                for p in self._brushingLayer:
                    p.lock()
                    p.image.fill(0)
                    p.imgVer = p.dataVer
                    p.unlock()
            else:
                rect = rect.intersected(viewportRect)
        
        tiling = self._tiling
        for tileId in tiling.intersected(rect):
            self._requestPatch(layerNr, tileId, tiling)
           
    def _cancelLayer(self,layer):
        for r in self._requestsOld[layer,:].flat:
            if r is not None:
                r.cancel()

    def _cancelAll(self):
        if self._requestsNew is not None:
            for r in self._requestsNew.flat:
                if r is not None:
                    r.cancel()
        if self._requestsOld is not None:
            for r in self._requestsOld.flat:
                if r is not None:
                    r.cancel()

    def _onStackChanged(self):
        self._initializePatches()
        self._invalidateRect()

    def _invalidateRect(self, rect = QRect()):
        if not self._renderThread:
            return

        if not rect.isValid():
            #everything is invalidated
            #we cancel all requests
            self._cancelAll()

            self._updatableTiles = []
            
            for p in self._brushingLayer:
                p.lock()
                p.image.fill(0)
                p.imgVer = p.dataVer
                p.unlock()
        
        tiling = self._tiling
        for tileId in tiling.intersected(rect):
            for l in range(self._numLayers):
                self._requestPatch(l, tileId, tiling)

                
    def drawForeground(self, painter, rect):
        if self._numLayers == 0 or not self._renderThread or not self._renderThread.isRunning():
            return

        patches = self._tiling.intersectedF(rect)

        for tileId in patches:
            p = self._brushingLayer[tileId]
            if p.dataVer == p.imgVer:
                continue

            p.paint(painter) #access to the underlying image patch is serialized
    
    def indicateSlicingPositionSettled(self, settled):
        self._dirtyIndicator.setVisible(settled)
        self._slicingPositionSettled = settled
   
    def drawBackground(self, painter, rect):
        if self._numLayers == 0 or not self._renderThread or not self._renderThread.isRunning():
            return

        #Find all patches that intersect the given 'rect'.

        patches = self._tiling.intersectedF(rect)

        for patchNr in patches:
            img = self._renderThread._compositeCurrent[patchNr]
            if img is None:
                continue
            painter.drawImage(self._tiling._imageRect[patchNr], img)

        #calculate progress information for 'pie' progress indicators on top
        #of each tile
        for tileId in patches:
            self._dirtyIndicator.setTileProgress(tileId, 1.0-self._numRequestsUnfinished(tileId)) 
                    
